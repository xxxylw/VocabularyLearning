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
    CheckInDayPayload,
    CheckInsResponse,
    DueReviewsResponse,
    MergeCheckInsRequest,
    PrepareJobRequest,
    PrepareJobResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    StudyCardResponse,
    StudyExampleResponse,
    StudySenseResponse,
    TodaySessionResponse,
    TodayStartRequest,
    TodaySummaryResponse,
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
            -- CROSS JOIN 钉死 words→entries→cards→reviews 的索引驱动
            -- 顺序（原因见 _all_books_progress_aggregates）。
            select 1
            from words
            cross join entries
            cross join cards
            cross join reviews
            where words.normalized_text = book_words.normalized_text
              and entries.word_id = words.id
              and cards.entry_id = entries.id
              and cards.user_id = ?
              and reviews.card_id = cards.id
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
                from words
                cross join entries
                cross join cards
                where words.normalized_text = book_words.normalized_text
                  and entries.word_id = words.id
                  and cards.entry_id = entries.id
                  and cards.user_id = ?
              )
              and not exists (
                select 1
                from words
                cross join entries
                cross join cards
                where words.normalized_text = book_words.normalized_text
                  and entries.word_id = words.id
                  and cards.entry_id = entries.id
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


def _all_books_progress_aggregates(
    connection, user_id: str
) -> dict[str, tuple[int, int, int]]:
    """Per-book progress aggregates for every book in one batched pass.

    Same semantics as _book_progress_aggregates (per book: total words;
    learned = 该用户的卡片至少有一条 review；mastered = 该用户在该词的
    全部卡片 mastered 且至少有一张卡), computed with three GROUP BY
    queries over the whole table instead of three correlated queries per
    book. 2026-09-07 修复：旧的逐书聚合（8 书 = 25 条 SQL，每条对
    book_words 逐行做跨 reviews/cards/entries/words 的相关子查询探测）
    在线上量级（8 书 ~41k 词）把 GET /api/books 推到 ~7s，撞前置网关
    5s 超时后表现为 502——书架在公网路径上完全不可用。
    """
    totals: dict[str, int] = {
        str(row["book_id"]): row["total"]
        for row in connection.execute(
            "select book_id, count(*) as total from book_words"
            " where book_id is not null group by book_id"
        )
    }
    learned: dict[str, int] = {
        str(row["book_id"]): row["total"]
        for row in connection.execute(
            """
            select bw.book_id as book_id,
                   count(distinct bw.normalized_text) as total
            from book_words bw
            where bw.book_id is not null
              and exists (
                -- CROSS JOIN 钉死 w→e→c→r 的驱动顺序：SQLite 的贪心
                -- 连接排序会退化为「每行按 user_id 扫全量 cards」（线上
                -- 量级 = 40k 行 × 6k 卡 ≈ 2.4 亿次探测），此处每段都有
                -- 索引（words.normalized_text 唯一索引 / idx_entries_word /
                -- idx_cards_entry_user / idx_reviews_card）。
                select 1
                from words w
                cross join entries e
                cross join cards c
                cross join reviews r
                where w.normalized_text = bw.normalized_text
                  and e.word_id = w.id
                  and c.entry_id = e.id
                  and c.user_id = ?
                  and r.card_id = c.id
              )
            group by bw.book_id
            """,
            (user_id,),
        )
    }
    mastered: dict[str, int] = {
        str(row["book_id"]): row["total"]
        for row in connection.execute(
            """
            select bw.book_id as book_id,
                   count(distinct bw.normalized_text) as total
            from book_words bw
            where bw.book_id is not null
              and exists (
                select 1
                from words w
                cross join entries e
                cross join cards c
                where w.normalized_text = bw.normalized_text
                  and e.word_id = w.id
                  and c.entry_id = e.id
                  and c.user_id = ?
              )
              and not exists (
                select 1
                from words w
                cross join entries e
                cross join cards c
                where w.normalized_text = bw.normalized_text
                  and e.word_id = w.id
                  and c.entry_id = e.id
                  and c.user_id = ?
                  and c.status <> 'mastered'
              )
            group by bw.book_id
            """,
            (user_id, user_id),
        )
    }
    book_ids = set(totals) | set(learned) | set(mastered)
    return {
        book_id: (
            totals.get(book_id, 0),
            learned.get(book_id, 0),
            mastered.get(book_id, 0),
        )
        for book_id in book_ids
    }


def list_books(user_id: str) -> BookListResponse:
    with connect() as connection:
        current_book_row, _fallback = resolve_current_book(connection, user_id)
        current_book_id = str(current_book_row["id"])
        book_rows = connection.execute(
            "select * from vocabulary_books order by created_at, id"
        ).fetchall()
        aggregates = _all_books_progress_aggregates(connection, user_id)
        books = []
        for row in book_rows:
            total, learned, mastered = aggregates.get(
                str(row["id"]), (0, 0, 0)
            )
            books.append(
                BookListItemResponse(
                    id=row["id"],
                    title=row["title"],
                    description=row["description"],
                    source=row["source"],
                    createdAt=row["created_at"],
                    updatedAt=row["updated_at"],
                    totalWords=total,
                    learnedWords=learned,
                    masteredWords=mastered,
                    fallbackNotice=None,
                    isCurrent=str(row["id"]) == current_book_id,
                )
            )
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

    # 2026-09-07 事故修复（database is locked）：旧实现把整批词包在**一个**
    # 事务里（单条 connect() 的 with 块），且 Oxford HTTP 拉取发生在事务内
    # ——首个写语句后写锁被持有到整批结束（词多 + 每词一次网络往返可达
    # 分钟级），期间其他连接的 migrate/读路径全部排队超时。现在：
    #   * 读阶段（选词）单独一个连接，只读不持锁；
    #   * enrichment HTTP 调用发生在任何事务之外（先读连接判断共享词条
    #     是否已存在，不存在才拉取）——慢网络只拖慢单词时延，不再持锁；
    #   * 每词一个短事务（词/词条/例句/卡片/状态一起提交），锁持有时间
    #     从「整批」降到「毫秒级每词」；中途失败时已完成的词保持 ready
    #     （幂等可重入），prepare_jobs 行仍只在全部处理后写入。
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
                    -- CROSS JOIN 钉死 words→entries→cards 的索引驱动顺序
                    -- （normalized 唯一索引点查 → (word_id, sense_order)
                    -- 点查 → (user_id, entry_id) 唯一点查）。旧写法让优化
                    -- 器以 cards(user_id=?) 为驱动整范围扫该用户所有卡，
                    -- 每个 book_words 词行重复一次：重书（~6500 词 ×
                    -- 3 万卡）实测 105s，today/start 的 merge 路径和
                    -- prepare 端点都会被网关 5s 超时掐断。
                    select 1
                    from words
                    cross join entries
                    cross join cards
                    where words.normalized_text = book_words.normalized_text
                      and entries.word_id = words.id
                      and cards.entry_id = entries.id
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

        # 共享词条是否已存在：读连接即可判断（WAL 下读不持锁、也不被写
        # 方阻塞）。overwriteExisting 会删除词条（见下），因此总是重新
        # 拉取。HTTP 拉取发生在下面写事务开启之前。
        if request.overwriteExisting:
            needs_prepare = True
        else:
            with connect() as connection:
                shared_entries = connection.execute(
                    """
                    select 1
                    from entries
                    join words on words.id = entries.word_id
                    where words.normalized_text = ?
                    limit 1
                    """,
                    (normalized_text,),
                ).fetchone()
            needs_prepare = shared_entries is None
        senses = provider.prepare(word_text, max_senses) if needs_prepare else None

        with connect() as connection:
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
                # 正常路径 senses 已在事务外拉取；None 只会出现在与并发
                # prepare 的窗口竞态里（词条在检查之后才消失），保持原
                # 语义回退到 provider。
                if senses is None:
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

    with connect() as connection:
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
    # P0 2026-09-08 「再来一组」：extraNewWords is folded into the daily
    # new-word allowance for this call only — the merge path appends
    # fresh new cards up to the resulting remaining quota. We do NOT
    # persist extra anywhere; each daily snapshot re-derives its
    # allowance from the study date's review history, so 加练 has no
    # cross-day residue.
    extra_new_words = request.extraNewWords

    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        snapshot_exists = _today_queue_snapshot_exists(
            connection, user_id, book_id, study_date
        )

    if not snapshot_exists:
        # 每日首次进入 Today：生成当天固定队列快照（复习卡在前 + 新卡在后）。
        _create_today_queue_snapshot(
            user_id, study_date, request.dailyNewWordTarget, extra_new_words
        )
    else:
        # 快照已存在：当日不重算，只把额度内新 prepare 就绪的新卡追加到队尾。
        _merge_new_cards_into_today_queue(
            user_id, study_date, request.dailyNewWordTarget, extra_new_words
        )

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
    user_id: str,
    study_date: date,
    daily_new_word_target: int,
    extra_new_words: int = 0,
) -> None:
    review_cards = sorted(
        # PRD ch.8 rule 2: review cards by due_at ascending (overdue
        # first); the stable sort keeps the book-sequence order as the
        # tie-breaker for cards sharing a due date.
        _get_due_review_cards(study_date, user_id),
        key=lambda card: card.dueAt,
    )

    # P0 2026-09-08 「再来一组」：extra is folded into the new-word
    # allowance. On a fresh day (no reviews yet) this is simply
    # target + extra, so the snapshot can size up the new-card pool to
    # accommodate an extra group requested before the first
    # start call completes. Quota bookkeeping still derives from the
    # study date's review history (see _count_new_words_studied_on),
    # so cross-day residue stays zero.
    new_word_target_remaining = max(
        daily_new_word_target
        + extra_new_words
        - _count_new_words_studied_on(user_id, study_date),
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
    user_id: str,
    study_date: date,
    daily_new_word_target: int,
    extra_new_words: int = 0,
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
        # 已入队的卡（无论 review / new 类型）都不能再作为 fresh 新卡
        # 追加：一个多义词可能以 review 类型入队、其主卡又是未复习的
        # 新卡，只按 new 类型过滤会把同一 (user, book, date, card_id)
        # 再插一次，撞 idx_today_queue_card 唯一索引（2026-09-07 生产
        # 日志 IntegrityError 的单线程可复现根因；快照路径的
        # review_card_ids 过滤一直是对的，这里对齐它）。
        queued_any_card_ids = {
            row["card_id"]
            for row in connection.execute(
                "select card_id from today_queue"
                " where user_id = ? and book_id = ? and study_date = ?",
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
                  -- P1 2026-09-08：UTC 日界竞态修复——改用 study_date
                  -- 列（服务器本地日期，与 today_queue.study_date 同口径）。
                  and reviews.study_date = ?
              )
            """,
            (user_id, book_id, study_date.isoformat(), study_date.isoformat()),
        ).fetchone()["total"]

    # Quota consumed today = distinct new words studied (reviews) UNION
    # queued new entries; a queued new entry already reviewed today is in
    # both sets, hence the subtraction below (PRD ch.8 rule 7).
    studied_new = _count_new_words_studied_on(user_id, study_date)
    # P0 2026-09-08 「再来一组」: extra is the *delta* the user wants to
    # add right now (one click = one group's worth of fresh new cards,
    # independent of the daily quota). The daily quota path stays
    # untouched below; we simply fold the extra into the total budget
    # before fetching candidates. Additivity across multiple clicks
    # falls out naturally because each call passes a fresh delta —
    # e.g. clicking 再来一组 twice with a group size of 2 expands the
    # queue by 2 + 2 cards, with no per-day ceiling other than the
    # remaining pool.
    quota_remaining = max(
        daily_new_word_target
        - studied_new
        - (len(queued_new_card_ids) - reviewed_queued_new),
        0,
    )
    total_to_add = quota_remaining + extra_new_words
    if total_to_add <= 0:
        return

    candidates = _get_due_new_cards(
        study_date, total_to_add + len(queued_new_card_ids), user_id
    )
    # 跨池新词抵扣：多义词的 new 侧主卡在快照创建时被按词去重、以
    # review 类型入队（见 _create_today_queue_snapshot 的 review_card_ids
    # 过滤）。它们当日首次复习即计入新词学习（_count_new_words_studied_on
    # 的口径），与 new 类型入队卡同样消耗当日配额。旧公式漏抵扣这批卡，
    # merge 会误判「额度未满、池子已耗尽」，进而同步跑
    # prepare_book_words —— prepare 选词对重书是百秒级查询，直接把
    # today/start 拖过网关 5s 超时（2026-09-07 基准 kaoyan-shanguo：
    # merge 路径 106s，其中 prepare 105s）。
    cross_pool_queued = sum(
        1
        for card in candidates
        if card.cardId in queued_any_card_ids
        and card.cardId not in queued_new_card_ids
    )
    total_to_add -= cross_pool_queued
    if total_to_add <= 0:
        return
    fresh_cards = [
        card for card in candidates if card.cardId not in queued_any_card_ids
    ]
    if len(fresh_cards) < total_to_add:
        # The quota grew mid-day but the pool has no ready new cards left:
        # prepare the missing words, mirroring the snapshot-creation path.
        prepare_book_words(
            user_id,
            PrepareJobRequest(
                scope="next",
                count=total_to_add - len(fresh_cards),
                maxSensesPerWord=5,
                overwriteExisting=False,
            ),
        )
        candidates = _get_due_new_cards(
            study_date, total_to_add + len(queued_new_card_ids), user_id
        )
        fresh_cards = [
            card for card in candidates if card.cardId not in queued_any_card_ids
        ]
    fresh_cards = fresh_cards[:total_to_add]
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
        # 前面 get_current_book_id 链路（ensure_default_book /
        # set_current_book_pointer）的 DML 在 Python sqlite3 默认
        # isolation_level 下可能已隐式开启事务；先把它们提交掉，避免
        # 显式 BEGIN IMMEDIATE 撞 "cannot start a transaction within a
        # transaction"（review_card 的 BEGIN IMMEDIATE 是 with 块第一条
        # 语句所以无此问题）。
        if connection.in_transaction:
            connection.commit()
        # BEGIN IMMEDIATE：读 max(position) 到写完的整段在一个写事务里
        # 原子完成。旧实现读位置时不持写锁，两个并发 today/start（网关
        # 超时后前端重试是常见来源）会各自算出相同的起始 position，
        # 后提交方撞 (user, book, date, position) 或
        # (user, book, date, card_id) 唯一索引直接 500。
        connection.execute("BEGIN IMMEDIATE")
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
            # insert or ignore：同一卡在重试/并发下重复追加时静默跳过
            # （唯一索引 idx_today_queue_card 兜底幂等），而不是让整个
            # today/start 500。
            connection.execute(
                """
                insert or ignore into today_queue (
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
                  -- P1 2026-09-08：UTC 日界竞态修复——见 services.py
                  -- _resolve_study_date 注释；该列取代 substr
                  -- (reviewed_at,1,10) 的 UTC 前缀，避免北京 0-8 点
                  -- 复习被错排到前一天。
                  and reviews.study_date = ?
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
                from words
                cross join entries
                cross join cards
                -- CROSS JOIN 钉死 words→entries→cards 的索引驱动顺序；
                -- due_at / status 作为索引后过滤条件（见
                -- _study_cards_from_rows 的同型注释）。
                where words.normalized_text in ({word_placeholders})
                  and entries.word_id = words.id
                  and cards.entry_id = entries.id
                  and cards.due_at <= ?
                  and cards.user_id = ?
                  and cards.status in ('new', 'learning', 'mastered')
                """,
                (*pending_words, study_date.isoformat(), user_id),
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


def get_today_summary(
    user_id: str, study_date: date | None = None
) -> TodaySummaryResponse:
    # P0 2026-09-08 跨设备完成态恢复：read-only twin of
    # _read_today_queue_session. Where the session read filters to the
    # pending subset (due_at <= study_date, status in new/learning/
    # mastered) so card mode only sees unfinished work, the summary
    # read returns the *completed* subset with no due filter — the
    # reviewed cards have already moved their due_at into the future
    # via SM-2 and must be fetched as-is so the spelling practice list
    # can replay them on a freshly refreshed device.
    study_date = study_date or date.today()
    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        queue_rows = connection.execute(
            "select card_id, position, queue_type from today_queue"
            " where user_id = ? and book_id = ? and study_date = ? order by position",
            (user_id, book_id, study_date.isoformat()),
        ).fetchall()

        if not queue_rows:
            return TodaySummaryResponse(
                studyDate=study_date,
                totalCards=0,
                reviewedCards=0,
                dayCompleted=False,
                completedCards=[],
            )

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
                  -- P1 2026-09-08：UTC 日界竞态修复——同 _read_today_queue_session
                  -- 的 reviewed_ids：study_date 列取代 substr 前缀。
                  and reviews.study_date = ?
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
        day_completed = total_cards > 0 and reviewed_cards >= total_cards

        completed_rows = [
            row
            for row in queue_rows
            if row["card_id"] in existing_ids and row["card_id"] in reviewed_ids
        ]

        completed_cards: list[StudyCardResponse] = []
        if completed_rows:
            completed_card_ids = [row["card_id"] for row in completed_rows]
            completed_placeholders = ", ".join("?" for _ in completed_card_ids)
            completed_words = [
                row["normalized_text"]
                for row in connection.execute(
                    f"""
                    select distinct words.normalized_text
                    from cards
                    join entries on entries.id = cards.entry_id
                    join words on words.id = entries.word_id
                    where cards.id in ({completed_placeholders})
                    """,
                    tuple(completed_card_ids),
                ).fetchall()
            ]
            if completed_words:
                word_placeholders = ", ".join("?" for _ in completed_words)
                # No `cards.due_at <= ?` filter here — reviewed cards
                # have already been rescheduled into the future. The
                # status filter stays the same as the session read so
                # we never surface an orphan card.
                due_rows = connection.execute(
                    f"""
                    select
                        cards.id as card_id,
                        cards.last_reviewed_at,
                        words.normalized_text
                    from words
                    cross join entries
                    cross join cards
                    where words.normalized_text in ({word_placeholders})
                      and entries.word_id = words.id
                      and cards.entry_id = entries.id
                      and cards.user_id = ?
                      and cards.status in ('new', 'learning', 'mastered')
                    """,
                    (*completed_words, user_id),
                ).fetchall()
                study_cards = _study_cards_from_rows(connection, due_rows, user_id)
                cards_by_id = {card.cardId: card for card in study_cards}
                for row in completed_rows:
                    card = cards_by_id.get(row["card_id"])
                    if card is None:
                        continue
                    card.queueType = row["queue_type"]
                    card.queuePosition = row["position"]
                    completed_cards.append(card)

        return TodaySummaryResponse(
            studyDate=study_date,
            totalCards=total_cards,
            reviewedCards=reviewed_cards,
            dayCompleted=day_completed,
            completedCards=completed_cards,
        )


def get_due_reviews(user_id: str, due_date: date) -> DueReviewsResponse:
    cards = _get_due_study_cards(due_date, None, user_id)
    return DueReviewsResponse(date=due_date, total=len(cards), cards=cards)


# ---------------------------------------------------------------------------
# P1 2026-09-08 打卡热点图服务端化（task 7683154325467565322）。
# ---------------------------------------------------------------------------

# 「新词」判定口径：该卡的**首次** review 落在这一 study_date。该口径
# 只依赖 reviews 表本身，天然覆盖所有完成路径（bug 存活期间的会话、
# API 层面完成、跨书共享词），对 today_queue 快照缺失的历史数据也成立。
_CHECK_IN_NEW_WORDS_SQL = """
select study_date, count(*) as new_cards from (
    select card_id, min(study_date) as study_date
    from reviews
    where user_id = ?
    group by card_id
)
group by study_date
"""

# 本地历史打卡的上传承载：只存「服务端当天没有 reviews」的日期（本地
# 独有历史，如旧本地版应用迁移过来的记录）。派生数据（reviews 聚合）
# 永远优先于该 override —— reviews 是完成判定的唯一权威。
CHECK_IN_OVERRIDES_KEY = "check_in_overrides"

# merge 端点单次上报的日期条数上限：真实 localStorage 记录按日去重，
# 个人学习数年内量级 ≤ 数千；超出视为异常请求直接 400。
MAX_MERGE_RECORDS = 2000


def _load_check_in_overrides(connection, user_id: str) -> dict[str, dict]:
    """读取某用户的本地历史上传承载（user_settings JSON）。"""
    row = connection.execute(
        "select value from user_settings where user_id = ? and key = ?",
        (user_id, CHECK_IN_OVERRIDES_KEY),
    ).fetchone()
    if row is None:
        return {}
    try:
        parsed = json.loads(row["value"])
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    overrides: dict[str, dict] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        try:
            date.fromisoformat(key)
        except ValueError:
            continue
        overrides[key] = {
            "completedCards": max(0, int(value.get("completedCards", 0))),
            "newCards": max(0, int(value.get("newCards", 0))),
            "reviewCards": max(0, int(value.get("reviewCards", 0))),
            "completedAt": str(value.get("completedAt", "")),
        }
    return overrides


def _derived_check_in_days(connection, user_id: str) -> list[CheckInDayPayload]:
    """从 reviews 按 study_date 聚合派生每日打卡记录。

    - completedCards = 当日 distinct card_id 数（服务端同一卡同日只允许
      一条 review，distinct 兜底未来口径变化）；
    - newCards = 首次 review 落在该日的卡数；
    - reviewCards = completedCards - newCards；
    - completedAt = 当日 max(reviewed_at)（ISO 字符串）。
    """
    derived_rows = connection.execute(
        """
        select study_date,
               count(distinct card_id) as completed_cards,
               max(reviewed_at) as completed_at
        from reviews
        where user_id = ?
        group by study_date
        """,
        (user_id,),
    ).fetchall()
    new_card_rows = connection.execute(
        _CHECK_IN_NEW_WORDS_SQL, (user_id,)
    ).fetchall()
    new_cards_by_date = {
        row["study_date"]: int(row["new_cards"]) for row in new_card_rows
    }

    records: list[CheckInDayPayload] = []
    for row in derived_rows:
        study_date = row["study_date"]
        completed = int(row["completed_cards"])
        new_cards = int(new_cards_by_date.get(study_date, 0))
        records.append(
            CheckInDayPayload(
                date=study_date,
                completedCards=completed,
                newCards=new_cards,
                reviewCards=max(0, completed - new_cards),
                completedAt=row["completed_at"] or "",
            )
        )
    return records


def get_check_ins(user_id: str) -> CheckInsResponse:
    """GET /api/check-ins 的服务端派生：reviews 聚合 + 本地历史 override。"""
    with connect() as connection:
        records = _derived_check_in_days(connection, user_id)
        derived_dates = {record.date.isoformat() for record in records}
        for study_date, override in _load_check_in_overrides(
            connection, user_id
        ).items():
            # 服务端当天已有 reviews 时派生值优先，丢弃该 override。
            if study_date in derived_dates:
                continue
            records.append(CheckInDayPayload(date=study_date, **override))
    records.sort(key=lambda record: record.date.isoformat())
    return CheckInsResponse(checkIns=records)


def merge_check_ins(user_id: str, request: MergeCheckInsRequest) -> CheckInsResponse:
    """POST /api/check-ins/merge：一次性合并浏览器本地的历史打卡。

    合并口径（逐日）：
    1. 当天服务端已有 reviews → 本地记录直接忽略（派生数据是唯一
       权威，覆盖一切完成路径，数值上 ≥ 本地记录）；
    2. 当天服务端没有 reviews（本地独有历史，如旧本地版数据）→ 存入
       user_settings 的 check_in_overrides 承载，与已有 override 按
       字段取 max（completedAt 取字典序最大），重复上报幂等。
    返回合并后的完整列表（与 GET 同形），客户端可直接整表替换。
    """
    if len(request.checkIns) > MAX_MERGE_RECORDS:
        raise ValueError(
            f"Too many check-in records: {len(request.checkIns)} > {MAX_MERGE_RECORDS}"
        )

    with connect() as connection:
        derived_dates = {
            row["study_date"]
            for row in connection.execute(
                "select distinct study_date from reviews where user_id = ?",
                (user_id,),
            ).fetchall()
        }
        overrides = _load_check_in_overrides(connection, user_id)
        for record in request.checkIns:
            study_date = record.date.isoformat()
            if study_date in derived_dates:
                continue
            existing = overrides.get(study_date)
            if existing is None:
                overrides[study_date] = {
                    "completedCards": record.completedCards,
                    "newCards": record.newCards,
                    "reviewCards": record.reviewCards,
                    "completedAt": record.completedAt,
                }
            else:
                overrides[study_date] = {
                    "completedCards": max(
                        existing["completedCards"], record.completedCards
                    ),
                    "newCards": max(existing["newCards"], record.newCards),
                    "reviewCards": max(existing["reviewCards"], record.reviewCards),
                    "completedAt": max(existing["completedAt"], record.completedAt),
                }
        connection.execute(
            "insert or replace into user_settings (user_id, key, value)"
            " values (?, ?, ?)",
            (user_id, CHECK_IN_OVERRIDES_KEY, json.dumps(overrides, sort_keys=True)),
        )

    return get_check_ins(user_id)


def review_card(
    user_id: str, card_id: str, request: ReviewCardRequest
) -> ReviewCardResponse:
    # P1 2026-09-08（task 7683097747100093410）UTC 日界竞态修复：
    # 旧实现 reviewed_on = request.reviewedDate or request.reviewedAt.date()，
    # 后者在 0-8 点窗口（UTC 日界）取的是前一天，与 today_queue.study_date
    # 分裂，导致「跨书共享词」复习被错排到前一天 / 当日 reviewedCards
    # 永远差 1 / 后续提交 409。统一为 _resolve_study_date：
    # reviewedDate 优先（客户端本地日期，与队列口径一致），否则把
    # reviewedAt（UTC ISO）转服务器本地日期。
    reviewed_on = _resolve_study_date(
        request.reviewedDate, request.reviewedAt
    )
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
                next_due_at,
                study_date
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                reviewed_on.isoformat(),
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
                  -- P1 2026-09-08：UTC 日界竞态修复——改用 study_date
                  -- 列（服务器本地日期），同时 previous_reviews 的 < 比较
                  -- 也切到该列。
                  and reviews.study_date = ?
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
                    -- CROSS JOIN 钉死 entries→cards→reviews 的索引驱动
                    -- 顺序（idx_entries_word_sense_order → idx_cards_entry
                    -- → idx_reviews_user_card，全部点查）。旧写法把
                    -- previous_reviews 放在驱动位、以 user_id 过滤，SQLite
                    -- 为其建 AUTOMATIC INDEX 后每个外层行仍要全量扫该
                    -- 用户的全部复习记录 —— 用户复习越多该查询越慢，是
                    -- today/start 慢查询的组成部分之一。
                    select 1
                    from entries previous_entries
                    cross join cards previous_cards
                    cross join reviews previous_reviews
                    where previous_entries.word_id = entries.word_id
                      and previous_cards.entry_id = previous_entries.id
                      and previous_cards.user_id = ?
                      and previous_reviews.card_id = previous_cards.id
                      and previous_reviews.user_id = ?
                      and previous_reviews.study_date < ?
                  )
                group by words.normalized_text
            )
            """,
            (
                user_id,
                study_date.isoformat(),
                book_id,
                user_id,
                user_id,
                study_date.isoformat(),
            ),
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
        from words
        cross join entries
        cross join cards
        -- CROSS JOIN 钉死 words→entries→cards 的索引驱动顺序（normalized
        -- 唯一索引 → idx_entries_word_sense_order → idx_cards_entry）。
        -- 旧写法让优化器自由选择连接顺序，实际计划以 cards(user_id=?) 为
        -- 驱动全量扫该用户所有卡，再对每行做 normalized_text IN 过滤——
        -- 队列响应构建（today/start 主链路）随用户卡总量线性变慢。
        where words.normalized_text in ({normalized_placeholders})
          and entries.word_id = words.id
          and cards.entry_id = entries.id
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
          -- P1 2026-09-08：UTC 日界竞态修复——同 _resolve_study_date
          -- 口径，study_date 列与 reviewed_on 完全一致。
          and study_date = ?
        limit 1
        """,
        (card_id, reviewed_on.isoformat()),
    ).fetchone()
    return row is not None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_study_date(
    reviewed_date: date | None, reviewed_at: datetime
) -> date:
    """Return the server-local study date for a review submission.

    Prefers the client-supplied ``reviewedDate`` (it always matches the
    today_queue's local-date basis). Falls back to converting
    ``reviewedAt`` (which the client always sends as a UTC ISO string
    via ``Date.toISOString()``) into the server-local calendar date.
    Naive datetimes are treated as UTC.

    The returned date is the single source of truth for both
    ``reviewed_on`` (used in the SM-2 due check and same-day
    de-duplication) and the new ``reviews.study_date`` column; the
    two previously diverged in the 00:00-08:00 Beijing window because
    ``request.reviewedAt.date()`` is a UTC date.
    """
    if reviewed_date is not None:
        return reviewed_date
    normalized = reviewed_at
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone().date()


def _create_enrichment_provider():
    source = os.environ.get("VOCAB_ENRICHMENT_SOURCE", "oxford").lower()
    if source == "fallback":
        return FallbackEnrichmentProvider()
    return OxfordEnrichmentProvider()
