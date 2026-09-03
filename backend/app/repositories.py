from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import re
from uuid import uuid4

from app.books import (
    DEFAULT_BOOK_ID,
    book_exists,
    ensure_default_book,
    get_current_book_id,
    upsert_book,
)
from app.db import connect
from app.models import BookProgressResponse, ImportBookWordsResponse


def normalize_word(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def import_book_words_csv(
    file_bytes: bytes,
    source_name: str,
    replace_existing: bool,
    *,
    book_id: str = DEFAULT_BOOK_ID,
    book_title: str | None = None,
    book_description: str | None = None,
    book_source: str | None = None,
) -> ImportBookWordsResponse:
    """Import an ordered word-list CSV into book_words (PRD ch.6/ch.10).

    Required CSV headers: ``sequence_index``, ``word``. Optional header:
    ``layer`` (分层标注列 — 必考词 / 基础词 / 超纲词 etc., stored verbatim
    in the new ``layer`` column; any further columns are ignored).

    Rows are attributed to ``book_id`` (default: the default book). For a
    non-default book the ``vocabulary_books`` row is created on the fly from
    ``book_title`` / ``book_description`` / ``book_source`` (title required
    when the book does not exist yet), which keeps the bookshelf rule
    「数据未就绪不入架」: the book row appears exactly when its words do.
    """
    csv_text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames or {"sequence_index", "word"} - set(reader.fieldnames):
        raise ValueError("CSV must include sequence_index and word headers")
    has_layer = "layer" in (reader.fieldnames or [])

    now = _utc_now()
    with connect() as connection:
        if book_id == DEFAULT_BOOK_ID:
            ensure_default_book(connection)
        elif book_exists(connection, book_id):
            pass
        elif not book_title:
            raise ValueError(
                f"Book {book_id!r} does not exist and no book_title was provided"
            )
        else:
            upsert_book(
                connection,
                book_id,
                title=book_title,
                description=book_description,
                source=book_source,
                now=now,
            )
        source = connection.execute(
            "select id from sources where type = ? and name = ? order by created_at limit 1",
            ("csv", source_name),
        ).fetchone()
        if source is None:
            source_id = str(uuid4())
            connection.execute(
                """
                insert into sources (id, type, name, path_or_url, metadata_json, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (source_id, "csv", source_name, None, None, now),
            )
        else:
            source_id = source["id"]

        if replace_existing:
            connection.execute(
                "delete from book_words where source_id = ? and book_id = ?",
                (source_id, book_id),
            )

        existing_rows = connection.execute(
            """
            select sequence_index, normalized_text from book_words
            where source_id = ? and book_id = ?
            """,
            (source_id, book_id),
        ).fetchall()
        seen_sequences = {row["sequence_index"] for row in existing_rows}
        seen_normalized = {row["normalized_text"] for row in existing_rows}

        imported = 0
        skipped = 0
        for row in reader:
            try:
                sequence_index = int((row.get("sequence_index") or "").strip())
            except ValueError:
                skipped += 1
                continue

            word_text = (row.get("word") or "").strip()
            normalized_text = normalize_word(word_text)
            if (
                not word_text
                or sequence_index in seen_sequences
                or normalized_text in seen_normalized
            ):
                skipped += 1
                continue

            connection.execute(
                """
                insert into book_words (
                    id,
                    source_id,
                    book_id,
                    sequence_index,
                    word_text,
                    normalized_text,
                    part_of_speech,
                    definition,
                    definition_source,
                    chinese_note,
                    import_status,
                    layer,
                    created_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    source_id,
                    book_id,
                    sequence_index,
                    word_text,
                    normalized_text,
                    None,
                    None,
                    None,
                    None,
                    "pending",
                    (row.get("layer") or "").strip() or None if has_layer else None,
                    now,
                    now,
                ),
            )
            seen_sequences.add(sequence_index)
            seen_normalized.add(normalized_text)
            imported += 1

    return ImportBookWordsResponse(
        sourceId=source_id,
        imported=imported,
        skipped=skipped,
        needsReview=0,
    )


def import_book_words_markdown(
    file_bytes: bytes, source_name: str, replace_existing: bool
) -> ImportBookWordsResponse:
    markdown_text = file_bytes.decode("utf-8-sig")
    csv_rows = ["sequence_index,word"]
    for line in markdown_text.splitlines():
        match = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", line)
        if match is None:
            continue
        sequence_index = match.group(1)
        word = match.group(2).replace('"', '""')
        csv_rows.append(f'{sequence_index},"{word}"')

    return import_book_words_csv(
        "\n".join(csv_rows).encode("utf-8"),
        source_name=source_name,
        replace_existing=replace_existing,
    )


def get_book_progress() -> BookProgressResponse:
    with connect() as connection:
        book_id = get_current_book_id(connection)
        row = connection.execute(
            """
            select
                count(*) as total_words,
                min(
                    case
                        when import_status in ('pending', 'needs_review')
                        then sequence_index
                    end
                ) as next_sequence_index
            from book_words
            where book_id = ?
            """,
            (book_id,),
        ).fetchone()

    return BookProgressResponse(
        totalWords=row["total_words"],
        nextSequenceIndex=row["next_sequence_index"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
