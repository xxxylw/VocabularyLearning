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
    next_due_at text not null,
    -- P1 2026-09-08（task 7683097747100093410）跨书共享词 UTC 日界竞态
    -- 修复：服务器本地日期（Asia/Shanghai），与 today_queue.study_date
    -- 同口径；旧查询用 substr(reviewed_at,1,10) 取的是客户端 UTC 日期，
    -- 北京 0-8 点复习会被错排到前一天，导致 reviewedCards 永远差 1。
    -- 旧库由 reviews_study_date_migration 补列并按 reviewed_at 回填。
    study_date text not null
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

-- 当日重复池（会话层循环，与跨天 SM-2 调度彻底分离）。当日队列中的
-- new 卡评 New（unknown）即入池，间隔 3 张重新出现，Got it 一次才
-- 移出；重复卡上的操作只更新本表，不写 reviews、不改 EF / 间隔 /
-- due_at。池随当日队列快照（user, book, study_date）持久化：刷新 /
-- 换设备不丢，跨 02:00 会话不中断；02:00 后重进随旧快照作废。
--   repeat_count    数据面：该卡当日已重新出现且再评非 Got it 的次数
--                   （初评为 0；达 3 上限置 capped 自动移出，D3）；
--   defer_remaining 会话层插入间隔（D2：间隔 3 张）：还要再展示多少
--                   张卡后该重复卡重新出现 —— 每展示一张当日队列卡
--                   （写一条 review）或一张池内卡（一次池操作）减 1，
--                   到 0 即下次出现。重进后按该计数确定性重算插入位置。
-- card_id 有意不挂 FK，与 today_queue 同口径（prepare-overwrite 删卡
-- 时读路径按 cards 存在性过滤，不炸外键）。
CREATE TABLE IF NOT EXISTS today_repeat_pool (
    id text primary key,
    user_id text not null references users(id),
    book_id text not null,
    study_date text not null,
    card_id text not null,
    repeat_count integer not null default 0,
    defer_remaining integer not null default 3,
    status text not null check (status in ('pending', 'cleared', 'capped')),
    created_at text not null,
    updated_at text not null,
    unique (user_id, book_id, study_date, card_id)
);

CREATE INDEX IF NOT EXISTS idx_today_repeat_pool_user_day
ON today_repeat_pool (user_id, book_id, study_date, status);

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
-- v3 (V3-01/V3-08): source extends to trial / alipay / wechat / mock /
-- super-synthesized; status 启用 trialing. Legacy databases gain the
-- remark / order_no columns via ALTER in app.db.migrate (they are
-- audit/linkage only — the read path keeps answering from the latest
-- row's status + expires_at, unchanged).
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
    remark text null,
    order_no text null,
    created_at text not null,
    updated_at text not null
);

-- v3 (V3-03/V3-08): payment orders. One row per 下单 attempt; the
-- payable amount is snapshotted at creation (backend 按订阅快照计算应
-- 收金额写入 orders) and the callback MUST match it before the order
-- is confirmed (金额不符不确认入账). status: pending → paid / closed
-- (收银台 15 分钟超时自动关单 / 用户取消支付) / failed (网关下单失败).
CREATE TABLE IF NOT EXISTS orders (
    id text primary key,
    out_trade_no text not null unique,
    user_id text not null references users(id),
    plan text not null,
    amount_cents integer not null,
    currency text not null default 'CNY',
    status text not null check (status in ('pending', 'paid', 'closed', 'failed')),
    channel text not null default 'wechat',
    pay_url text null,
    pay_qr_url text null,
    transaction_id text null,
    created_at text not null,
    updated_at text not null,
    paid_at text null
);

CREATE INDEX IF NOT EXISTS idx_orders_user_created
ON orders (user_id, created_at);

-- v3 (V3-03): payment callback archive — every notify payload is
-- stored verbatim (原始报文留档) with the processing result, for
-- troubleshooting and reconciliation.
CREATE TABLE IF NOT EXISTS payment_callbacks (
    id text primary key,
    out_trade_no text null,
    payload_json text not null,
    result text not null,
    created_at text not null
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
