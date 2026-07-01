from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import re
from uuid import uuid4

from app.db import connect
from app.models import BookProgressResponse, ImportBookWordsResponse


def normalize_word(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def import_book_words_csv(
    file_bytes: bytes, source_name: str, replace_existing: bool
) -> ImportBookWordsResponse:
    csv_text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames or {"sequence_index", "word"} - set(reader.fieldnames):
        raise ValueError("CSV must include sequence_index and word headers")

    now = _utc_now()
    with connect() as connection:
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
                "delete from book_words where source_id = ?",
                (source_id,),
            )

        existing_rows = connection.execute(
            "select sequence_index, normalized_text from book_words where source_id = ?",
            (source_id,),
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
                    sequence_index,
                    word_text,
                    normalized_text,
                    part_of_speech,
                    definition,
                    definition_source,
                    chinese_note,
                    import_status,
                    created_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    source_id,
                    sequence_index,
                    word_text,
                    normalized_text,
                    None,
                    None,
                    None,
                    None,
                    "pending",
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


def get_book_progress() -> BookProgressResponse:
    with connect() as connection:
        row = connection.execute(
            """
            select
                count(*) as total_words,
                min(sequence_index) as next_sequence_index
            from book_words
            """
        ).fetchone()

    return BookProgressResponse(
        totalWords=row["total_words"],
        nextSequenceIndex=row["next_sequence_index"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
