# VocabularyLearning Design Spec

## Summary

VocabularyLearning is a local-first IELTS vocabulary study app for studying the user's local copy of `雅思词汇真经PDF高清版.pdf` in book order. The app runs at `localhost`, stores all study data in SQLite, and has two main uses:

- Web study: each day the learner presses one Today button and enters an immersive Anki-like card session.
- Full-book Anki export: after the full book has been prepared locally, the app exports one complete `.apkg` deck.

The confirmed architecture is a Unified Prepare Job. The same preparation pipeline supports today's next words, a sequence range, and the full book. Review and export never call network, OCR, dictionary, or AI providers; they only read prepared local data.

## Architecture

The app uses:

- Frontend: Vite, React, TypeScript.
- Backend: FastAPI, Python.
- Database: SQLite.
- Anki export: Python `genanki`.

The backend owns imports, preparation jobs, scheduling, persistence, and Anki export. The frontend owns the Today entry point, immersive card study flow, review feedback, readiness views, and settings.

## Data Model

Core tables:

- `sources`: provenance for the primary book, manual imports, enrichment providers, and generated content.
- `book_words`: ordered book vocabulary stream. It stores `sequence_index`, `word_text`, `normalized_text`, and import status. It does not store complex multi-sense card content.
- `words`: normalized vocabulary items.
- `entries`: one IELTS-relevant sense of one word. One word may have up to five selected study senses by default.
- `entry_examples`: one or more IELTS-level examples for one sense. One example may be marked primary.
- `cards`: the review/export unit. One card corresponds to one `entries` row.
- `reviews`: review history and Ebbinghaus transitions.

Relationship:

```text
sources
-> book_words
-> words
-> entries
-> entry_examples
-> cards
-> reviews
```

Card rule:

```text
one selected sense = one web review card = one Anki card
```

For example, `charge` may produce up to five cards: fee/payment, ask to pay, formal accusation, charging a battery, and responsibility/control.

## Book Import

Version 1 imports the book sequence from `book_words.csv`.

Minimum format:

```csv
sequence_index,word
1,abandon
2,ability
3,abroad
```

Extended format can include `part_of_speech`, `definition`, and `chinese_note`, but multi-sense content is generated after import and stored in `entries` and `entry_examples`.

The PDF is a scanned file without an extractable text layer, so OCR is treated as an upstream preprocessing step that produces CSV. The repository must not commit extracted copyrighted book content.

## Prepare Jobs

Prepare Job is the central content-generation workflow. It supports:

- `next`: prepare the next N book words.
- `range`: prepare a sequence range.
- `full_book`: prepare the complete book deck.

Each prepared word should:

1. Normalize or create a `words` row.
2. Select at most five IELTS-relevant high-frequency senses.
3. Store selected senses in `entries`.
4. Store one or more IELTS-level examples in `entry_examples`.
5. Create one `cards` row per selected sense.
6. Mark incomplete items as `needs_review`.

Prepare Job may use configured official dictionary APIs, AI providers, manual data, or experimental local-only HTML parsing. Oxford Learner's Dictionaries HTML scraping is not a default stable provider. If used experimentally, it must be labelled `experimental_html`, rate-limited, cached locally, and replaceable.

## API Design

Local routes live under `/api`.

Import:

```http
POST /api/book-words/import
```

Prepare:

```http
POST /api/prepare-jobs
GET /api/prepare-jobs/{jobId}
```

Prepare request:

```json
{
  "sourceId": "uuid",
  "scope": "full_book",
  "count": null,
  "rangeStart": null,
  "rangeEnd": null,
  "maxSensesPerWord": 5,
  "overwriteExisting": false
}
```

Today:

```http
POST /api/study/today/start
```

The endpoint builds today's session from:

- the next `daily_new_word_target` book words
- Ebbinghaus due review cards

If the next book words are not prepared, the endpoint starts or returns a prepare job before the immersive session begins.

Review:

```http
GET /api/reviews/due
POST /api/cards/{cardId}/reviews
```

Export:

```http
POST /api/export/anki/full-book
```

Export is offline-only. If the full book is not prepared, it reports readiness gaps instead of preparing content during export.

## Scheduling

Baseline intervals:

```text
[0, 1, 2, 4, 7, 15, 30]
```

Feedback:

- `known`: advance to the next interval; mark mastered after the final interval.
- `uncertain`: keep the same stage and schedule for tomorrow.
- `unknown`: reset to stage 0 and schedule for tomorrow in Version 1.

The user never manually chooses due dates. Feedback updates the next review date automatically.

## Frontend Experience

The visual direction is a warm local study desk: paper-toned background, soft white surfaces, sage primary actions, muted blue/plum/gold accents, clear typography, and restrained density. It should feel friendly and focused, not like a marketing page.

Main views:

- Today
- Immersive Study Session
- Prepare
- Library
- Export
- Settings

Today has one primary action: `Start today cards`.

Today shows only enough context to start:

- new book words count
- due review count
- total session cards
- estimated time
- preparation readiness

After pressing `Start today cards`, the learner enters an immersive card page. This page hides sidebar navigation and non-study tools. It keeps only:

- Exit
- progress
- remaining new/review count
- card front
- reveal button
- card back
- Known / Uncertain / Unknown feedback

Card front:

- word
- part of speech
- sense label

Card back:

- English definition
- primary IELTS-level example
- optional additional examples
- Chinese note at the bottom, visually secondary

Keyboard flow:

- Space reveals the answer.
- 1 records Known.
- 2 records Uncertain.
- 3 records Unknown.

## Full-Book Anki Export

Version 1 does not export daily Anki packages. Daily study happens in the web app.

The export page is for the full prepared book deck only. Export stays disabled until the full book's selected cards are ready locally.

Anki card shape:

- Front: word, part of speech, optional sense label.
- Back: English definition, IELTS example, optional additional examples, and secondary Chinese note.

## Testing Strategy

Use TDD in vertical slices. Tests should verify behavior through public interfaces, not private implementation details.

Priority behaviors:

- CSV import creates ordered `book_words`.
- Prepare next N creates words, entries, examples, and cards.
- Today start combines new prepared cards and due review cards.
- Review feedback updates stage, due date, and review history.
- Full-book export refuses when the deck is incomplete.
- Full-book export creates a non-empty `.apkg` when ready.

## Issue Breakdown Preview

Implementation should be split with `/to-issues` into thin vertical slices:

1. Import `book_words.csv` and show book readiness.
2. Prepare next N words into sense cards.
3. Start Today session with new and due cards.
4. Record card feedback and update Ebbinghaus schedule.
5. Prepare full book with resumable job status.
6. Export full-book `.apkg` offline.
7. Apply warm immersive frontend design.

Each slice should be independently demoable and testable.
