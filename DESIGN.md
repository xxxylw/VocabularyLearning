# VocabularyLearning Design

## Goal

VocabularyLearning is a local-first IELTS vocabulary study app. It runs in the browser at `localhost`, helps the learner study the local book `D:\_materials\Ielts\docs\雅思词汇真经PDF高清版.pdf` in order, enriches words with English definitions and IELTS-level example sentences, schedules review with an Ebbinghaus-style cadence, and exports the prepared full-book card deck to Anki `.apkg`.

The first version is for personal local study. It does not include accounts, cloud sync, payment, sharing, or a public dictionary service.

## Product Scope

### Version 1

- Add daily words from the next items in the user's local IELTS book sequence, using the user's daily new word target as the count.
- Add extra daily words by typing or pasting one word per line.
- Build the local IELTS book sequence through a preprocessing step, backed by a `book_words` table.
- Import the local IELTS book sequence from a `book_words.csv` file in Version 1.
- When enrichment finds many senses for one word, select at most five IELTS-relevant high-frequency senses for study cards by default.
- Prepare all selected senses, English definitions, and IELTS-level examples in the local database before study/export.
- Create word cards with:
  - word
  - part of speech
  - English definition
  - IELTS-level example sentence
  - optional Chinese note
  - source metadata
- Review due cards for today.
- Record review feedback:
  - `known`
  - `uncertain`
  - `unknown`
- Schedule reviews using a fixed Ebbinghaus baseline:
  - day 1
  - day 2
  - day 4
  - day 7
  - day 15
  - day 30
- Export the prepared full-book card deck to Anki `.apkg`, with one Anki card per selected sense.
- Store all app data locally.

### Later

- Import additional user-owned IELTS word lists from PDF, CSV, TXT, or manual extraction.
- Add spelling tests and cloze tests.
- Add multiple example sentence styles: IELTS Writing Task 2, Speaking Part 2, Speaking Part 3.
- Add daily progress charts.
- Add optional sync after the local app is stable.

## Technical Stack

### Recommended Stack

- Frontend: Vite + React + TypeScript.
- Styling: plain CSS modules or Tailwind CSS.
- Backend: FastAPI + Python.
- Database: SQLite.
- Migrations: Alembic or a small explicit migration runner for the first version.
- Anki export: Python `genanki`.
- Definition providers:
  - primary: configurable official dictionary API, such as Oxford Dictionaries API if credentials and license permit it.
  - fallback: user-entered definition or another licensed/open API.
- Example sentence provider:
  - local template generator for offline fallback.
  - optional OpenAI-compatible API for higher-quality IELTS examples.

### Why This Stack

Python is a better fit for `.apkg` export because `genanki` is mature and simple. SQLite is built into Python and is enough for a single-user local app. React keeps the card review UI responsive without making the backend complex.

This uses two local processes during development:

- frontend: `http://localhost:5173`
- backend: `http://localhost:8000`

For a packaged local version, FastAPI can serve the built frontend so the user only opens one address.

## Domain Terms

- Word: the raw vocabulary item, such as `load`, `beneficial`, or `undermine`.
- Entry: dictionary-like information for one word, including part of speech and definition.
- Sense: one specific meaning of a word, including part of speech and English definition.
- Study Example: an example sentence attached to a sense. It should be suitable for IELTS learners.
- Card: the study object shown to the learner. A word can have multiple cards if it has multiple senses.
- Review: one attempt to recall or recognize a card.
- Review Schedule: the future due dates for a card.
- Deck Export: an Anki `.apkg` file generated from the prepared full-book card deck.
- Source: where a definition or example came from, such as manual input, dictionary API, AI generation, or imported user-owned document.

## Data Model

### `words`

Stores the normalized word.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `text` | text | original word text |
| `normalized_text` | text | lowercase, trimmed lookup key |
| `created_at` | datetime | local timestamp |
| `updated_at` | datetime | local timestamp |

Unique index: `normalized_text`.

### `entries`

Stores one sense for a word.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `word_id` | text | FK to `words.id` |
| `sense_order` | integer | order within the word entry |
| `part_of_speech` | text | noun, verb, adjective, etc. |
| `definition` | text | English definition |
| `definition_source` | text | `manual`, `oxford_api`, `open_api`, `imported` |
| `chinese_note` | text nullable | optional learner note |
| `created_at` | datetime | local timestamp |
| `updated_at` | datetime | local timestamp |

Recommended index: `(word_id, sense_order)`.

### `entry_examples`

Stores one or more study examples for a sense.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `entry_id` | text | FK to `entries.id` |
| `example_order` | integer | order within the sense |
| `sentence` | text | IELTS-level or learner-dictionary example sentence |
| `source` | text | `manual`, `oxford_api`, `ai`, `template`, `imported`, `experimental_html` |
| `is_primary` | integer | 1 if shown by default on the card back |
| `created_at` | datetime | local timestamp |
| `updated_at` | datetime | local timestamp |

Recommended index: `(entry_id, example_order)`.

### `cards`

Stores review state.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `entry_id` | text | FK to `entries.id` |
| `status` | text | `new`, `learning`, `mastered`, `suspended` |
| `stage` | integer | current Ebbinghaus interval index |
| `due_at` | date | next review date |
| `created_on` | date | study day when card was created |
| `last_reviewed_at` | datetime nullable | latest review timestamp |

### `reviews`

Stores review history.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `card_id` | text | FK to `cards.id` |
| `rating` | text | `known`, `uncertain`, `unknown` |
| `reviewed_at` | datetime | local timestamp |
| `previous_stage` | integer | stage before feedback |
| `next_stage` | integer | stage after feedback |
| `next_due_at` | date | next due date after feedback |

### `sources`

Stores optional provenance for imported or generated content.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `type` | text | `pdf`, `api`, `manual`, `ai` |
| `name` | text | display name |
| `path_or_url` | text nullable | local path or provider URL |
| `metadata_json` | text nullable | provider-specific metadata |
| `created_at` | datetime | local timestamp |

### `book_words`

Stores the ordered word stream prepared from the user's local IELTS book or a manually curated source.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | text | UUID |
| `source_id` | text | FK to `sources.id` |
| `sequence_index` | integer | 1-based order in the book sequence |
| `word_text` | text | extracted or curated word |
| `normalized_text` | text | lowercase, trimmed lookup key |
| `part_of_speech` | text nullable | prefilled when available |
| `definition` | text nullable | prefilled when available |
| `definition_source` | text nullable | `manual`, `ocr`, `oxford_api`, `ai`, `experimental_html` |
| `chinese_note` | text nullable | optional learner note |
| `import_status` | text | `pending`, `ready`, `needs_review` |
| `created_at` | datetime | local timestamp |
| `updated_at` | datetime | local timestamp |

Unique index: `(source_id, sequence_index)`.

Recommended index: `(source_id, normalized_text)`.

## Review Scheduling

Baseline intervals in days:

```text
[0, 1, 2, 4, 7, 15, 30]
```

The first card is due immediately on its creation date.

Feedback behavior:

- `known`: move to the next interval. If the card passes the final interval, mark it `mastered`.
- `uncertain`: keep the same stage and schedule the next review for tomorrow.
- `unknown`: reset to stage 0 and schedule the next review for today or tomorrow, depending on the user's setting. Version 1 uses tomorrow to avoid endless same-day loops.

This is intentionally simpler than Anki SM-2. The app can later support an adaptive scheduler without changing the card UI.

## API Design

All API routes are local-only under `/api`.

### Health

```http
GET /api/health
```

Response:

```json
{
  "ok": true,
  "version": "0.1.0"
}
```

### Add Words

```http
POST /api/words/batch
```

### Add Next Book Words

```http
POST /api/book-words/add-next
```

Request:

```json
{
  "sourceId": "uuid",
  "count": 20,
  "createdOn": "2026-07-01"
}
```

### Import Book Words CSV

```http
POST /api/book-words/import
```

Request:

Multipart form upload with:

- `file`: CSV file
- `sourceName`: display name, default `雅思词汇真经`
- `replaceExisting`: boolean, default `false`

Minimum CSV:

```csv
sequence_index,word
1,abandon
2,ability
3,abroad
```

Extended CSV:

```csv
sequence_index,word,part_of_speech,definition,chinese_note
1,abandon,verb,to leave somebody or something,放弃；遗弃
```

Response:

```json
{
  "sourceId": "uuid",
  "imported": 3000,
  "skipped": 0,
  "needsReview": 12
}
```

Response:

```json
{
  "created": [],
  "skipped": [],
  "nextSequenceIndex": 121
}
```

Request:

```json
{
  "words": ["load", "beneficial", "undermine"],
  "createdOn": "2026-07-01",
  "definitionMode": "auto",
  "exampleMode": "auto"
}
```

Response:

```json
{
  "created": [
    {
      "wordId": "uuid",
      "cardId": "uuid",
      "word": "beneficial",
      "status": "created"
    }
  ],
  "skipped": [
    {
      "word": "load",
      "reason": "already_exists"
    }
  ],
  "needsAttention": [
    {
      "word": "undermine",
      "reason": "definition_not_found"
    }
  ]
}
```

### List Cards

```http
GET /api/cards?status=learning&due=2026-07-01
```

Query parameters:

- `status`: optional `new`, `learning`, `mastered`, `suspended`
- `due`: optional date. Returns cards due on or before this date.
- `q`: optional word search.

Response:

```json
{
  "cards": [
    {
      "cardId": "uuid",
      "word": "beneficial",
      "partOfSpeech": "adjective",
      "definition": "helpful, useful, or good",
      "examples": [
        {
          "exampleId": "uuid",
          "sentence": "Regular feedback is beneficial for students preparing for IELTS writing.",
          "isPrimary": true
        }
      ],
      "chineseNote": "",
      "status": "learning",
      "stage": 2,
      "dueAt": "2026-07-03"
    }
  ]
}
```

### Update Card

```http
PATCH /api/cards/{cardId}
```

Request:

```json
{
  "partOfSpeech": "adjective",
  "definition": "helpful, useful, or good",
  "examples": [
    {
      "sentence": "Regular feedback is beneficial for students preparing for IELTS writing.",
      "isPrimary": true
    }
  ],
  "chineseNote": "useful; good for something"
}
```

Response:

```json
{
  "cardId": "uuid",
  "updated": true
}
```

### Review Card

```http
POST /api/cards/{cardId}/reviews
```

Request:

```json
{
  "rating": "known",
  "reviewedAt": "2026-07-01T20:30:00+08:00"
}
```

Response:

```json
{
  "cardId": "uuid",
  "rating": "known",
  "previousStage": 1,
  "nextStage": 2,
  "nextDueAt": "2026-07-03",
  "status": "learning"
}
```

### Today's Review Queue

```http
GET /api/reviews/due?date=2026-07-01
```

Response:

```json
{
  "date": "2026-07-01",
  "total": 12,
  "cards": []
}
```

The `cards` shape matches `GET /api/cards`.

### Enrich One Word

```http
POST /api/enrichment/word
```

Request:

```json
{
  "word": "load",
  "definitionProvider": "auto",
  "exampleProvider": "auto",
  "exampleLevel": "ielts"
}
```

Response:

```json
{
  "word": "load",
  "entries": [
    {
      "partOfSpeech": "noun",
      "definition": "something that is being carried or supported",
      "definitionSource": "dictionary_api",
      "examples": [
        {
          "sentence": "The government should reduce the financial load on low-income families.",
          "source": "ai",
          "isPrimary": true
        }
      ]
    }
  ],
  "warnings": []
}
```

### Export Anki Package

```http
POST /api/export/anki
```

Request:

```json
{
  "scope": "full_book",
  "deckName": "IELTS Vocabulary Book",
  "includeChineseNote": true,
  "cardUnit": "sense"
}
```

Response:

```json
{
  "downloadUrl": "/api/export/anki/files/ielts-vocabulary-book.apkg",
  "cardCount": 12000
}
```

Download:

```http
GET /api/export/anki/files/{fileName}
```

Anki card shape:

- Front: word, part of speech, and optional sense label.
- Back: English definition, primary IELTS-level example sentence, optional additional examples, and a visually secondary Chinese note at the bottom.
- Export unit: one card per selected `entries` row, not one card per raw word.
- Export must be offline: it reads only local `words`, `entries`, `entry_examples`, `cards`, and `reviews` data. It must not call dictionary, AI, OCR, or web providers while generating `.apkg`.
- Version 1 does not export daily Anki packages. Daily study happens in the web app; Anki export is for the complete prepared book deck.

## Enrichment Providers

The app must not depend on scraping Oxford Learner's Dictionary pages as its default design. Web pages can change, and dictionary content is licensed.

Provider strategy:

1. If configured, use an official dictionary API.
2. If no dictionary API is configured, create a card with empty definition fields and mark it as needing attention.
3. Let the learner fill or edit the definition manually.
4. Generate IELTS-level example sentences only from the word plus known meaning when enough context exists.

The Oxford Learner's Dictionaries website can be queried manually in a browser, but the app should not depend on scraping Oxford HTML as the default enrichment path. If implemented for personal experimentation, it must be explicitly labelled `experimental_html`, rate-limited, cache results locally, and remain replaceable by the official API/manual/AI providers.

When a source returns many senses for the same word, the enrichment pipeline should rank and keep at most five study senses by default. IELTS-relevant senses should be preferred over rare, literary, old-use, humorous, or highly specialized senses.

The app stores source labels so the learner can tell whether content is manual, imported, API-provided, or AI-generated.

Enrichment timing:

1. The user starts today's study batch.
2. The backend selects the next `daily_new_word_target` words from `book_words`.
3. The backend prepares local study content for each selected word before creating review cards:
   - choose at most five IELTS-relevant senses
   - store each sense in `entries`
   - store one or more study examples in `entry_examples`
   - mark incomplete senses as `needs_review`
4. Review cards are generated only from locally stored senses.
5. Anki export uses the same local card data and does not perform network enrichment.

## PDF Import Boundary

The user owns a local PDF word book at `D:\_materials\Ielts\docs\雅思词汇真经PDF高清版.pdf`. Version 1 is centered on studying this book in order.

The checked PDF appears to be a scanned file without an extractable text layer, so the app cannot rely on ordinary PDF text extraction for the Book Sequence.

Version 1 may support a local preprocessing step that uses OCR or a manually prepared local word list to build the Book Sequence. The app should store only the extracted vocabulary records in the user's local database, not committed book content.

The first supported preprocessing output is `book_words.csv`. OCR work should produce this CSV shape before importing into SQLite.

When PDF import is added:

- The app should process only local files selected by the user.
- Imported text should be treated as personal study material.
- The app should avoid committing extracted copyrighted book content into the repository.
- The import pipeline should save only the learner's local database records.

## UI Design

### Visual Direction

The UI should feel like a warm local study desk: paper-toned background, soft white surfaces, sage primary actions, muted blue/plum/gold accents, clear typography, and restrained density. It should be friendly and focused, not a landing page or marketing surface.

The product should keep the learner's attention on the next study action. Today has one primary action: start today's cards.

### Main Views

- Today: show today's new book-word count, due review count, total session cards, estimated time, preparation readiness, and a single `Start today cards` entry point.
- Immersive Study Session: Anki-like full-page card flow with no sidebar or export distractions.
- Prepare: run and monitor `next`, `range`, or `full_book` prepare jobs.
- Library: searchable table of words, senses, examples, cards, and needs-review items.
- Export: prepare and download the full-book `.apkg`.
- Settings: configure daily new word target, dictionary provider, AI provider, database path, and review behavior.

### Card Layout

Front:

- word
- optional part of speech

Back:

- English definition
- IELTS example sentence or selected learner-dictionary example
- optional Chinese note
- source label

The Chinese note should be visually secondary and placed at the bottom of the card back.

Controls:

- reveal answer
- known
- uncertain
- unknown
- edit card

### Today Session Flow

When the learner presses `Start today cards`, the app builds a single session queue from:

- the next `daily_new_word_target` book words
- review cards due under the Ebbinghaus schedule

If the next book words are not prepared, the app starts or resumes a prepare job before entering the card session.

The study page is immersive. It hides sidebar navigation and non-study tools, keeps progress visible, and supports keyboard flow:

- Space: reveal answer
- 1: known
- 2: uncertain
- 3: unknown

## Local Files

Default local runtime files:

```text
VocabularyLearning/
  backend/
  frontend/
  data/
    vocabulary.sqlite
    exports/
```

`data/` should be ignored by Git except for a `.gitkeep`, because it contains personal study data and generated Anki packages.

## Configuration

Backend environment variables:

```text
VOCAB_DB_PATH=./data/vocabulary.sqlite
VOCAB_EXPORT_DIR=./data/exports
OXFORD_APP_ID=
OXFORD_APP_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
```

The app should run without Oxford or OpenAI keys. Missing keys only disable automatic enrichment.

## Error Handling

- Duplicate words should be skipped, not treated as failures.
- Failed dictionary lookup should create a card that needs manual completion.
- Failed AI example generation should leave the example empty and show a warning.
- Anki export should fail clearly if there are no matching cards.
- Database errors should return a structured local API error and be logged in the backend console.

## Testing Strategy

Backend tests:

- word normalization
- duplicate handling
- Ebbinghaus scheduling transitions
- card review history creation
- Anki export creates a non-empty `.apkg`
- enrichment fallback when providers are missing

Frontend tests:

- Today start creates or resumes the correct study session
- immersive study reveal shows definition, examples, and secondary Chinese note
- review buttons send the correct rating
- card edit form preserves existing fields
- full-book export calls the export endpoint only when the deck is ready

End-to-end smoke test:

1. Start backend and frontend.
2. Import a small `book_words.csv`.
3. Prepare the next three book words.
4. Start today's card session.
5. Review one card as `known`.
6. Prepare the full sample book deck.
7. Export the full-book deck after its study content is prepared.
8. Confirm `.apkg` downloads.

## First Implementation Plan

1. Scaffold `backend/` with FastAPI, SQLite access, and tests.
2. Implement migrations and core tables.
3. Implement `book_words.csv` import.
4. Implement Prepare Job for `next` scope with local/manual fallback content.
5. Implement Today session creation from new book cards plus due review cards.
6. Implement review scheduling and feedback.
7. Implement full-book readiness checks and offline `.apkg` export.
8. Scaffold `frontend/` with the warm Today entry and immersive study session.
9. Connect the frontend to local APIs.
10. Add enrichment provider interfaces with manual fallback.
11. Add optional AI example generation.
12. Add documentation for local startup.
