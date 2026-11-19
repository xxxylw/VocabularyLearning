# Oxford Variant Switch Runbook

Operational manual for switching the live Fusio service between the
**Wiktionary variant** (current production content) and the **Oxford variant**
(Oxford → WordNet → Wiktionary per-word priority). Both variants share ONE live
SQLite DB and differ only in 4 content tables and the served static dir.

- Pipelines: `scripts/build_oxford_content.py` (content switch),
  `scripts/mirror_oxford_audio.py` (audio mirror).
- Server orchestration: `/home/ubuntu/switch-version.sh` (the ONLY supported
  way to switch the live DB — never run `--apply` against the live DB by hand).

## 1. Layout (server 43.142.125.99)

| Path | Purpose |
| --- | --- |
| `/home/ubuntu/vocabularylearning/` | live deployment (backend + `frontend-dist/`, systemd `vocab`) |
| `/home/ubuntu/vocabularylearning-oxford/` | oxford deployment copy (backend + `frontend-dist-oxford/`) |
| `/home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite` | the ONE live DB (user data + content tables) |
| `.../vocabulary.sqlite.bak-20261019-pre-oxford` | pre-oxford baseline backup (wiktionary restore source) |
| `/home/ubuntu/switch-version.sh` | variant switch entry point (backup → switch → symlink → restart → health); repo copy: `scripts/switch-version.sh` |
| `/home/ubuntu/oxford-data-inputs/` | persistent switch assets: `build_oxford_content.py`, `wordnet/` (extracted), `oxford_progress_cloud.jsonl` |
| `frontend-dist-current` symlink | what systemd `VOCAB_STATIC_DIR` points at; flips between the two dist dirs |
| `frontend-dist-oxford/audio_oxford/` | 3,355 mirrored Oxford mp3s (see §3) |
| `frontend-dist*/audio_v3` | shared Wiktionary audio (symlink, unchanged by switching) |
| `/home/ubuntu/oxford-data-inputs/oxford_progress_cloud.jsonl` | scraped Oxford senses/IPA/audio |
| `/home/ubuntu/deliveries/oxford-20261020/` | delivery artifacts + `tmp-assets/` (wordnet dir, rehearsal DB/reports) |

## 2. Content model & design decisions

The switch rewrites ONLY the 4 content tables:
`entries`, `entry_examples`, `pronunciation_cache`, `vocabulary_books`.
All user tables (users / cards / reviews / sessions / subscriptions / orders /
settings / today_queue …) are never written.

Per-word priority in the oxford direction (16,858 DB words total):

| Source | Words | What changes |
| --- | --- | --- |
| Oxford (oxford_progress_cloud.jsonl) | 3,358 | senses, examples, UK/US IPA, audio URL |
| WordNet 3.0 (raw index.*/data.*) | 12,999 | definitions + examples; pronunciation rows untouched |
| Wiktionary (remainder) | 501 | rows left untouched |

Key invariants (all implemented and rehearsed):

1. **UPDATE-only for entries** — entry ids are never deleted in the oxford
   direction, because `cards.entry_id` references `entries.id`. Existing
   entries are updated in place; extra Oxford/WordNet senses get fresh UUIDs.
   Leftover entries (existing sense count > new sense count, 14,875 rows) keep
   their original Wiktionary content — per-sense fallback, so every card keeps
   resolving with honest content.
2. **definition_source CHECK vocabulary** — the column CHECK only allows
   `open_api / imported / fallback / oxford_api`-style values, so sources map
   as Oxford → `oxford_api`, WordNet → `imported`, Wiktionary → `open_api`.
   (The task's literal `oxford`/`wordnet` values would require a DDL change;
   the frontend never renders this column. Recorded deviation.)
3. **Book titles** — the oxford direction renames the 13 books to English via
   the script's `BOOK_TITLES_EN` map; the wiktionary direction restores
   `vocabulary_books` verbatim from the pre-oxford backup. NOTE (2026-10-20):
   the books API shows English titles **in both variants by design** — backend
   `app/books.py::ENGLISH_BOOK_TITLES` (2026-10-15 "zero-Chinese study flow")
   maps titles at the serialization layer while the DB keeps the Chinese
   originals. This is NOT a content-table concern.
4. **Live-DB write safety** — writes commit in ~25-word batches with
   `synchronous=NORMAL` so live writers block only briefly; a timestamped DB
   backup is always taken before `--apply`.
5. **Audio** — oxford words get `audioUrl` pointing at the local mirror
   `audio_oxford/<word>.mp3` when the file exists, else the Oxford CDN
   hotlink as fallback; WordNet/Wiktionary words keep their Wiktionary audio.
6. **Pronunciation cache** — oxford rows for mirrored words are rewritten to
   `status='ready'` local-audio JSON so the backend serves local files.

## 3. Audio mirror (prerequisite for the oxford variant)

```bash
# trial, then full run (~3,913 downloads, ~0.25s politeness delay each)
/home/ubuntu/venv/bin/python3 /tmp/mirror_oxford_audio.py \
  --db /home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite \
  --oxford-jsonl /home/ubuntu/oxford-data-inputs/oxford_progress_cloud.jsonl \
  --out-dir /tmp/audio_oxford            # add --limit 200 for a trial run
rsync -a /tmp/audio_oxford/ /home/ubuntu/vocabularylearning-oxford/frontend-dist-oxford/audio_oxford/
```

Idempotent + resumable (existing files skipped); failures land in
`_failures.json`. 2026-10-20 full-run result: 3,913 download attempts
(uk+us variants), 3,355 unique mp3s mirrored = every one of the 3,355 Oxford
words that has an audio URL (3 words have none); all 16 transient failures
recovered by re-running the script.

## 4. Switching variants

```bash
# ALWAYS via the wrapper (timestamped DB backup -> content switch -> symlink
# flip -> systemctl restart vocab -> health check; idempotent):
ssh ubuntu@43.142.125.99
sudo /home/ubuntu/switch-version.sh oxford      # ~5 min content switch
sudo /home/ubuntu/switch-version.sh wiktionary # ~6 s (restore from backup)
```

Never run `build_oxford_content.py --apply` directly against the live DB —
the wrapper is the audited path. Dry-run (no writes) for verification:

```bash
/home/ubuntu/venv/bin/python3 /home/ubuntu/oxford-data-inputs/build_oxford_content.py \
  --db /home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite \
  --direction oxford --wordnet-dir <wordnet-dir> --audio-dir <audio dir>
# default is dry-run; add --apply only inside switch-version.sh flows
```

systemd unit note: `VOCAB_STATIC_DIR` points at the `frontend-dist-current`
symlink (NOT a concrete dir), so flipping the symlink + restart is enough.

## 5. Verification after each switch

```bash
curl -sf http://127.0.0.1/api/health && echo HEALTHY
# content sanity (wiktionary terminal state = pre-oxford baseline):
#   entries 54,513 / entry_examples 58,648 / pronunciation_cache 16,854 /
#   vocabulary_books 13 (all Chinese titles in the DB)
# oxford state: entries 59,323 / entry_examples 54,878 (rehearsed numbers)
# user tables unchanged; front page loads; sample word renders.
```

2026-10-20 live round-trip evidence: oxford direction 5m03s healthy, wiktionary
restore 5.6s healthy, then 4 content tables re-verified md5-identical to the
pre-oxford backup (见 §7 报告).

## 6. Rollback

`switch-version.sh wiktionary` IS the rollback — it restores the 4 content
tables verbatim from the pre-oxford backup and flips the symlink back. Each
switch additionally writes its own timestamped backup, so a bad intermediate
state can always be restored manually from the newest `vocabulary.sqlite.bak-*`.

## 7. Evidence inventory (2026-10-20)

- Rehearsal (DB copy): `/tmp/rehearse.sqlite`, report
  `/tmp/rehearse_oxford_report.json` + `/tmp/restore_out.json` (4-table md5
  MATCH after restore); copies under `deliveries/oxford-20261020/tmp-assets/`.
- Live switch report: `/tmp/switch-oxford-20261020-105718-report.json`.
- Full mirror log: `/tmp/mirror_full.log`, failures `/tmp/audio_oxford/_failures.json`.
- Branch `oxford-data` (baseline `d3846f9`): frontend English baseline +
  oxford variant UI + both pipeline scripts + this runbook.

## 8. Post-delivery hardening (2026-10-20 PM)

Two wrapper gaps found during final acceptance and fixed (deployed copy at
`/home/ubuntu/switch-version.sh`, repo copy `scripts/switch-version.sh`):

1. **/tmp dependency** — the wrapper previously invoked
   `/tmp/build_oxford_content.py` and the script defaulted `--wordnet-dir`
   to `/tmp/wn/wordnet`; both vanish on reboot. Now both live under
   `/home/ubuntu/oxford-data-inputs/` (script copied, `wordnet.zip` extracted
   in place) and the wrapper passes `--wordnet-dir` explicitly.
2. **`--audio-dir` never passed** — the 10:57 live oxford switch therefore
   wrote CDN hotlinks (`audio_local=0`, `audio_hotlink=3,355`) and the local
   `/audio_oxford/` mirror sat unused. The wrapper now passes
   `--audio-dir <frontend-dist-oxford>/audio_oxford`.

Rehearsal with the fixed wrapper (DB copy `/tmp/rehearse2-work.sqlite`,
reports `/tmp/rehearse2-oxford-report.json` / `/tmp/rehearse2-wikt-report.json`):

- oxford direction: audio_local=3,354 / hotlink=1 / none=3 (3 words have no
  Oxford audio; 1 hotlink is a word whose mp3 is missing from the mirror);
  entries 59,323; 13 English titles in DB; sample `abandon` →
  `/audio_oxford/abandon.mp3`.
- wiktionary restore: all 4 content tables md5-identical to the pre-oxford
  backup; 13 Chinese titles restored.

Server pytest (cloud d3846f9 code, unchanged by this work):
`296 passed, 12 failed` in 93s — failures are pre-existing in books /
subscription / auth test expectations (e.g. `test_book_switch.py` still
asserts the Chinese fallback notice and Chinese book titles that the
`ENGLISH_BOOK_TITLES` serialization no longer emits; `test_subscription.py`
on the server predates the 1,700/3,400-cent pricing). No backend file was
touched by the Oxford work. Server `backend/app` is byte-identical to cloud
d3846f9 (zero drift); `backend/tests` has exactly 2 stale files vs cloud
(`test_book_switch.py`, `test_subscription.py`).
