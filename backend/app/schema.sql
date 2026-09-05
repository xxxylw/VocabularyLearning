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
    -- Word-list layer annotation (PRD ch.10): 必考词 / 基础词 / 简单基础词 /
    -- 超纲词 for the 考研英语红宝书 import. Kept in the data layer only —
    -- no UI depends on it yet (分层选学 is a follow-up candidate).
    layer text null,
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

-- v2 cloud batch 2 (C-05/C-06): every card belongs to exactly one user.
-- Enrichment (entries / examples) stays in the shared layer, so two
-- users studying the same word each own their own card row — the
-- unique index is (user_id, entry_id) and is created in app.db.migrate()
-- (legacy databases get the column via ALTER TABLE there first).
CREATE TABLE IF NOT EXISTS cards (
    id text primary key,
    user_id text not null references users(id),
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
    user_id text not null references users(id),
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
    user_id text not null references users(id),
    book_id text not null,
    study_date text not null,
    position integer not null,
    card_id text not null,
    queue_type text not null check (queue_type in ('new', 'review')),
    created_at text not null,
    unique (user_id, book_id, study_date, position)
);

-- Snapshot header: marks "the queue for this book+date was generated",
-- even when that day's queue turned out empty. Per user in v2 batch 2.
CREATE TABLE IF NOT EXISTS today_queue_snapshots (
    user_id text not null references users(id),
    book_id text not null,
    study_date text not null,
    created_at text not null,
    primary key (user_id, book_id, study_date)
);

-- v2 cloud edition (batch 1): accounts, sessions and email tokens.
-- Per-user study data isolation is batch 2 and deliberately not
-- reflected here — existing study tables stay untouched.
CREATE TABLE IF NOT EXISTS users (
    id text primary key,
    email text not null unique,
    password_hash text not null,
    email_verified integer not null default 0,
    is_super integer not null default 0,
    created_at text not null,
    updated_at text not null
);

-- Opaque session tokens (C-02): only SHA-256 hashes are stored; the
-- raw token lives in the client's Authorization header.
CREATE TABLE IF NOT EXISTS sessions (
    id text primary key,
    user_id text not null references users(id),
    token_hash text not null unique,
    created_at text not null,
    expires_at text not null
);

-- Verify/reset tokens (C-05): 1h expiry, single use (used_at), stored
-- hashed like sessions.
CREATE TABLE IF NOT EXISTS email_tokens (
    id text primary key,
    user_id text not null references users(id),
    token_hash text not null unique,
    purpose text not null check (purpose in ('verify_email', 'reset_password')),
    expires_at text not null,
    used_at text null,
    created_at text not null,
    -- C-01a: wrong-submission counter for the 6-digit code scheme
    -- (5 wrong attempts void the code). Legacy link-era rows read as 0.
    attempts integer not null default 0
);

-- Per-user settings (C-05): current_book_id moved out of the global
-- settings table into here so two users never overwrite each other's
-- pointer. System-level flags (SM-2 backfill cursor/done) stay in
-- settings.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id text not null references users(id),
    key text not null,
    value text not null,
    primary key (user_id, key)
);

-- Subscriptions (C-09 data model, batch 2 schema / batch 3 endpoints):
-- independent table, price is configuration-driven, ``source`` marks
-- mock orders so real payment channels can be told apart later.
CREATE TABLE IF NOT EXISTS subscriptions (
    id text primary key,
    user_id text not null references users(id),
    plan text not null,
    status text not null check (status in ('active', 'expired', 'canceled', 'trialing')),
    price_cents integer not null,
    currency text not null default 'CNY',
    source text not null default 'mock',
    started_at text not null,
    expires_at text not null,
    auto_renew integer not null default 0,
    created_at text not null,
    updated_at text not null
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_book_words_source_sequence
ON book_words (source_id, sequence_index);

DROP INDEX IF EXISTS idx_book_words_source_normalized;

CREATE UNIQUE INDEX IF NOT EXISTS idx_book_words_source_normalized
ON book_words (source_id, normalized_text);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_word_sense_order
ON entries (word_id, sense_order);

-- idx_cards_entry and idx_today_queue_card live in app.db.migrate():
-- they index user_id, which legacy databases only gain after the
-- ALTER/rebuild steps there.
