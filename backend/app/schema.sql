CREATE TABLE IF NOT EXISTS sources (
    id text primary key,
    type text not null,
    name text not null,
    path_or_url text null,
    metadata_json text null,
    created_at text not null
);

-- Vocabulary books (P1): every book_words row belongs to exactly one book.
-- The default book (雅思词汇真经) is inserted by app.db.migrate().
CREATE TABLE IF NOT EXISTS vocabulary_books (
    id text primary key,
    title text not null,
    description text null,
    source text null,
    created_at text not null,
    updated_at text not null
);

CREATE TABLE IF NOT EXISTS book_words (
    id text primary key,
    source_id text not null references sources(id),
    book_id text null references vocabulary_books(id),
    sequence_index integer not null,
    word_text text not null,
    normalized_text text not null,
    part_of_speech text null,
    definition text null,
    definition_source text null check (
        definition_source is null
        or definition_source in ('manual', 'ocr', 'oxford_api', 'ai', 'experimental_html')
    ),
    chinese_note text null,
    import_status text not null check (import_status in ('pending', 'ready', 'needs_review')),
    created_at text not null,
    updated_at text not null
);

CREATE TABLE IF NOT EXISTS words (
    id text primary key,
    text text not null,
    normalized_text text not null unique,
    created_at text not null,
    updated_at text not null
);

CREATE TABLE IF NOT EXISTS entries (
    id text primary key,
    word_id text not null references words(id),
    sense_order integer not null,
    part_of_speech text not null,
    sense_label text not null default '',
    definition text not null,
    definition_source text not null check (
        definition_source in (
            'manual',
            'oxford_api',
            'open_api',
            'imported',
            'ai',
            'experimental_html',
            'fallback'
        )
    ),
    chinese_note text null,
    created_at text not null,
    updated_at text not null
);

CREATE TABLE IF NOT EXISTS entry_examples (
    id text primary key,
    entry_id text not null references entries(id),
    example_order integer not null,
    sentence text not null,
    source text not null check (
        source in (
            'manual',
            'oxford_api',
            'ai',
            'template',
            'imported',
            'experimental_html',
            'fallback'
        )
    ),
    is_primary integer not null,
    created_at text not null,
    updated_at text not null
);

CREATE TABLE IF NOT EXISTS cards (
    id text primary key,
    entry_id text not null references entries(id),
    status text not null check (status in ('new', 'learning', 'mastered', 'suspended')),
    stage integer not null,
    due_at text not null,
    created_on text not null,
    last_reviewed_at text null,
    -- SM-2 (P0-4): ease factor and current interval. The legacy stage
    -- column is kept as a historical field for rollback only and no
    -- longer participates in scheduling.
    ef real not null default 2.5,
    interval_days integer not null default 0
);

CREATE TABLE IF NOT EXISTS reviews (
    id text primary key,
    card_id text not null references cards(id),
    rating text not null check (rating in ('known', 'uncertain', 'unknown')),
    reviewed_at text not null,
    previous_stage integer not null,
    next_stage integer not null,
    next_due_at text not null
);

CREATE TABLE IF NOT EXISTS settings (
    key text primary key,
    value text not null
);

CREATE TABLE IF NOT EXISTS pronunciation_cache (
    normalized_word text primary key,
    response_json text not null,
    status text not null check (status in ('ready', 'unavailable')),
    retry_after text null,
    cached_at text not null
);

CREATE TABLE IF NOT EXISTS prepare_jobs (
    id text primary key,
    scope text not null,
    status text not null check (status in ('queued', 'running', 'completed', 'failed')),
    total_words integer not null,
    processed_words integer not null,
    ready_cards integer not null,
    needs_review integer not null,
    failed_words_json text not null,
    created_at text not null,
    updated_at text not null
);

-- Today study queue snapshot (P1 断点续传, PRD ch.8). One ordered queue
-- per book per study date; rows reference the word-group's primary card.
-- Pure-additive storage: deleting a snapshot never rewrites cards /
-- reviews / scheduling state. card_id intentionally carries no FK so
-- prepare-overwrite (which deletes cards) keeps working; the read path
-- simply drops rows whose card no longer exists.
CREATE TABLE IF NOT EXISTS today_queue (
    id text primary key,
    book_id text not null,
    study_date text not null,
    position integer not null,
    card_id text not null,
    queue_type text not null check (queue_type in ('new', 'review')),
    created_at text not null,
    unique (book_id, study_date, position)
);

-- Snapshot header: marks "the queue for this book+date was generated",
-- even when that day's queue turned out empty.
CREATE TABLE IF NOT EXISTS today_queue_snapshots (
    book_id text not null,
    study_date text not null,
    created_at text not null,
    primary key (book_id, study_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_today_queue_card
ON today_queue (book_id, study_date, card_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_book_words_source_sequence
ON book_words (source_id, sequence_index);

DROP INDEX IF EXISTS idx_book_words_source_normalized;

CREATE UNIQUE INDEX IF NOT EXISTS idx_book_words_source_normalized
ON book_words (source_id, normalized_text);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_word_sense_order
ON entries (word_id, sense_order);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_entry
ON cards (entry_id);
