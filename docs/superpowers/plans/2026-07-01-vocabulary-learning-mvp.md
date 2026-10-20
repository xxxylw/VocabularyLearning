# VocabularyLearning MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first MVP that imports `book_words.csv`, prepares local sense cards, starts an immersive Today session, records Ebbinghaus feedback, and exposes full-book Anki export readiness.

**Architecture:** FastAPI owns SQLite persistence, prepare jobs, scheduling, and export readiness. React owns the warm Today entry and immersive Anki-like card flow. The first implementation uses a deterministic local fallback enrichment provider so the complete app can be tested before Oxford/API/AI providers are added.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, Vite, React, TypeScript, CSS modules or plain CSS.

---

## File Structure

Create:

- `backend/pyproject.toml`: backend dependencies and pytest config.
- `backend/app/main.py`: FastAPI app and router registration.
- `backend/app/db.py`: SQLite connection and migration runner.
- `backend/app/schema.sql`: database schema.
- `backend/app/models.py`: Pydantic request/response models.
- `backend/app/scheduling.py`: Ebbinghaus transition logic.
- `backend/app/repositories.py`: database read/write helpers.
- `backend/app/enrichment.py`: deterministic fallback enrichment provider.
- `backend/app/services.py`: import, prepare, today session, review, and export readiness service functions.
- `backend/app/routes.py`: HTTP endpoints.
- `backend/tests/test_book_import.py`: CSV import behavior tests.
- `backend/tests/test_prepare_today_review.py`: prepare, Today session, and review behavior tests.
- `backend/tests/test_export_readiness.py`: full-book export readiness tests.
- `frontend/package.json`: frontend dependencies and scripts.
- `frontend/index.html`: Vite root.
- `frontend/src/main.tsx`: React root.
- `frontend/src/api.ts`: typed local API client.
- `frontend/src/App.tsx`: app shell and route state.
- `frontend/src/styles.css`: warm study desk visual system.
- `frontend/src/components/TodayView.tsx`: Today entry view.
- `frontend/src/components/StudySession.tsx`: immersive card session.
- `frontend/src/components/PrepareView.tsx`: prepare status/readiness view.
- `frontend/src/components/ExportView.tsx`: full-book export readiness view.

Modify:

- `DESIGN.md`: only if implementation reveals a naming conflict.
- `.gitignore`: add generated `backend/.venv`, `frontend/dist`, and local data if missing.

## Task 1: Backend Scaffold and Health Check

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/routes.py`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing health test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "0.1.0"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_health.py -v
```

Expected: FAIL because `app.main` does not exist.

- [ ] **Step 3: Add backend dependencies**

Create `backend/pyproject.toml`:

```toml
[project]
name = "vocabulary-learning-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn>=0.30",
  "python-multipart>=0.0.9",
  "pydantic>=2.8",
  "genanki>=0.13"
]

[project.optional-dependencies]
test = [
  "pytest>=8.2",
  "httpx>=0.27"
]

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 4: Implement the minimal app**

Create `backend/app/main.py`:

```python
from fastapi import FastAPI

from app.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="VocabularyLearning", version="0.1.0")
    app.include_router(router, prefix="/api")
    return app


app = create_app()
```

Create `backend/app/routes.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "version": "0.1.0"}
```

- [ ] **Step 5: Run the test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_health.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend
git commit -m "feat: scaffold backend health check"
```

## Task 2: SQLite Schema and CSV Import

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/schema.sql`
- Create: `backend/app/models.py`
- Create: `backend/app/repositories.py`
- Modify: `backend/app/routes.py`
- Test: `backend/tests/test_book_import.py`

- [ ] **Step 1: Write the failing CSV import test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_import_book_words_csv_creates_ordered_words(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    csv_bytes = b"sequence_index,word\n1,abandon\n2,ability\n"
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", csv_bytes, "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 2
    assert response.json()["skipped"] == 0
    assert response.json()["needsReview"] == 0

    progress = client.get("/api/book-words/progress").json()
    assert progress["totalWords"] == 2
    assert progress["nextSequenceIndex"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_book_import.py -v
```

Expected: FAIL because `/api/book-words/import` is not implemented.

- [ ] **Step 3: Add the schema**

Create `backend/app/schema.sql` with tables: `sources`, `book_words`, `words`, `entries`, `entry_examples`, `cards`, `reviews`, `settings`, and `prepare_jobs`. Include unique indexes on `words.normalized_text`, `(book_words.source_id, book_words.sequence_index)`, and `(book_words.source_id, book_words.normalized_text)`.

- [ ] **Step 4: Add database helpers**

Create `backend/app/db.py` with:

```python
import os
import sqlite3
from pathlib import Path


def db_path() -> Path:
    return Path(os.environ.get("VOCAB_DB_PATH", "./data/vocabulary.sqlite"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
```

- [ ] **Step 5: Implement import repository and route**

Implement:

- `normalize_word(text: str) -> str`
- `import_book_words_csv(file_bytes: bytes, source_name: str, replace_existing: bool) -> ImportBookWordsResponse`
- `get_book_progress() -> BookProgressResponse`

Expose:

```http
POST /api/book-words/import
GET /api/book-words/progress
```

- [ ] **Step 6: Run the test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_book_import.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/app backend/tests
git commit -m "feat: import book words csv"
```

## Task 3: Prepare Next N into Local Sense Cards

**Files:**
- Create: `backend/app/enrichment.py`
- Modify: `backend/app/services.py`
- Modify: `backend/app/routes.py`
- Test: `backend/tests/test_prepare_today_review.py`

- [ ] **Step 1: Write the failing prepare test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_prepare_next_creates_one_or_more_cards_per_word(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", b"sequence_index,word\n1,charge\n2,decline\n", "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "maxSensesPerWord": 5, "overwriteExisting": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["processedWords"] == 2
    assert body["readyCards"] >= 2
    assert body["needsReview"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_prepare_today_review.py::test_prepare_next_creates_one_or_more_cards_per_word -v
```

Expected: FAIL because `/api/prepare-jobs` is not implemented.

- [ ] **Step 3: Add deterministic fallback enrichment**

Create `backend/app/enrichment.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedSense:
    part_of_speech: str
    sense_label: str
    definition: str
    example: str
    chinese_note: str | None = None


class FallbackEnrichmentProvider:
    def prepare(self, word: str, max_senses: int) -> list[PreparedSense]:
        senses = [
            PreparedSense(
                part_of_speech="word",
                sense_label="general IELTS use",
                definition=f"A learner-friendly IELTS study meaning for '{word}'.",
                example=f"In IELTS writing, students should use '{word}' accurately and naturally.",
                chinese_note=None,
            )
        ]
        return senses[:max_senses]
```

- [ ] **Step 4: Implement prepare service**

Implement a synchronous MVP service that:

- selects the next unprepared `book_words`
- upserts `words`
- inserts `entries`
- inserts primary `entry_examples`
- inserts one `cards` row per entry with `status='learning'`, `stage=0`, and `due_at=today`
- records a completed `prepare_jobs` row

- [ ] **Step 5: Run the prepare test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_prepare_today_review.py::test_prepare_next_creates_one_or_more_cards_per_word -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app backend/tests
git commit -m "feat: prepare next book words"
```

## Task 4: Today Session and Ebbinghaus Review

**Files:**
- Create: `backend/app/scheduling.py`
- Modify: `backend/app/services.py`
- Modify: `backend/app/routes.py`
- Test: `backend/tests/test_prepare_today_review.py`

- [ ] **Step 1: Write the failing Today session test**

```python
def test_today_session_combines_ready_cards_and_records_known_review(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", b"sequence_index,word\n1,charge\n", "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post("/api/prepare-jobs", json={"scope": "next", "count": 1, "maxSensesPerWord": 5})

    session = client.post("/api/study/today/start", json={"date": "2026-07-01", "dailyNewWordTarget": 1}).json()

    assert session["totalCards"] >= 1
    card = session["cards"][0]
    assert card["word"] == "charge"
    assert card["definition"]
    assert card["examples"][0]["sentence"]

    review = client.post(f"/api/cards/{card['cardId']}/reviews", json={"rating": "known", "reviewedAt": "2026-07-01T09:00:00+08:00"}).json()

    assert review["previousStage"] == 0
    assert review["nextStage"] == 1
    assert review["nextDueAt"] == "2026-07-02"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_prepare_today_review.py::test_today_session_combines_ready_cards_and_records_known_review -v
```

Expected: FAIL because Today session and review endpoints are not implemented.

- [ ] **Step 3: Implement scheduler**

Create `backend/app/scheduling.py`:

```python
from datetime import date, timedelta

INTERVALS = [0, 1, 2, 4, 7, 15, 30]


def transition(stage: int, rating: str, reviewed_on: date) -> tuple[int, date, str]:
    if rating == "known":
        next_stage = min(stage + 1, len(INTERVALS) - 1)
        status = "mastered" if next_stage == len(INTERVALS) - 1 else "learning"
        return next_stage, reviewed_on + timedelta(days=INTERVALS[next_stage]), status
    if rating == "uncertain":
        return stage, reviewed_on + timedelta(days=1), "learning"
    if rating == "unknown":
        return 0, reviewed_on + timedelta(days=1), "learning"
    raise ValueError(f"Unsupported rating: {rating}")
```

- [ ] **Step 4: Implement Today and review routes**

Expose:

```http
POST /api/study/today/start
GET /api/reviews/due
POST /api/cards/{cardId}/reviews
```

Today response cards include:

- `cardId`
- `word`
- `partOfSpeech`
- `senseLabel`
- `definition`
- `examples`
- `chineseNote`
- `stage`
- `dueAt`
- `queueType`

- [ ] **Step 5: Run the test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_prepare_today_review.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app backend/tests
git commit -m "feat: start today session and review cards"
```

## Task 5: Full-Book Export Readiness

**Files:**
- Modify: `backend/app/services.py`
- Modify: `backend/app/routes.py`
- Test: `backend/tests/test_export_readiness.py`

- [ ] **Step 1: Write the failing readiness test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_full_book_export_refuses_until_all_book_words_are_prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", b"sequence_index,word\n1,charge\n2,decline\n", "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post("/api/prepare-jobs", json={"scope": "next", "count": 1, "maxSensesPerWord": 5})

    response = client.post("/api/export/anki/full-book", json={"deckName": "IELTS Vocabulary Book", "includeChineseNote": True})

    assert response.status_code == 409
    body = response.json()
    assert body["preparedWords"] == 1
    assert body["totalWords"] == 2
    assert body["missingWords"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_export_readiness.py -v
```

Expected: FAIL because export readiness is not implemented.

- [ ] **Step 3: Implement readiness check**

Implement `/api/export/anki/full-book` so it:

- counts total `book_words`
- counts distinct prepared book words that have at least one `cards` row through `words.normalized_text`
- returns HTTP 409 with readiness counts when incomplete
- returns HTTP 200 with `downloadUrl` and `cardCount` only when complete

For MVP, the 200 path may create a small `.apkg` with `genanki` from local cards.

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_export_readiness.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app backend/tests
git commit -m "feat: guard full book anki export readiness"
```

## Task 6: Frontend Warm Today Entry and Immersive Study Session

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/components/TodayView.tsx`
- Create: `frontend/src/components/StudySession.tsx`
- Create: `frontend/src/components/ExportView.tsx`

- [ ] **Step 1: Scaffold Vite React TypeScript**

Run:

```powershell
pnpm create vite frontend --template react-ts
```

Expected: `frontend/` contains a Vite React TypeScript app.

- [ ] **Step 2: Replace default app with local API client**

Create `frontend/src/api.ts` with typed functions:

```ts
export type StudyCard = {
  cardId: string;
  word: string;
  partOfSpeech: string;
  senseLabel: string;
  definition: string;
  examples: Array<{ exampleId: string; sentence: string; isPrimary: boolean }>;
  chineseNote: string | null;
  queueType: "new" | "review";
};

export async function startTodaySession(): Promise<{ totalCards: number; cards: StudyCard[] }> {
  const response = await fetch("/api/study/today/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dailyNewWordTarget: 20 }),
  });
  if (!response.ok) throw new Error("Unable to start today's session");
  return response.json();
}

export async function reviewCard(cardId: string, rating: "known" | "uncertain" | "unknown") {
  const response = await fetch(`/api/cards/${cardId}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating, reviewedAt: new Date().toISOString() }),
  });
  if (!response.ok) throw new Error("Unable to review card");
  return response.json();
}
```

- [ ] **Step 3: Implement Today and StudySession components**

`TodayView` shows one primary `Start today cards` button. `StudySession` hides navigation and shows one card with Reveal plus Known / Uncertain / Unknown buttons.

- [ ] **Step 4: Apply warm study desk CSS**

Use the confirmed design: paper background, soft white surfaces, sage primary button, muted blue/plum/gold accents, secondary Chinese note at the bottom of the card back.

- [ ] **Step 5: Run frontend build**

Run:

```powershell
cd frontend
pnpm install
pnpm build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend
git commit -m "feat: add warm immersive study frontend"
```

## Task 7: Local Startup and Smoke Test

**Files:**
- Create: `README.md`
- Modify: `backend/app/main.py`
- Modify: `frontend/vite.config.ts`

- [ ] **Step 1: Configure Vite proxy**

Set `/api` proxy to `http://localhost:8000`.

- [ ] **Step 2: Write README startup instructions**

Include:

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000

cd frontend
pnpm dev
```

Include smoke test:

1. Import a small `book_words.csv`.
2. Prepare next words.
3. Start Today cards.
4. Reveal a card.
5. Mark Known.
6. Confirm schedule advances.
7. Confirm full-book export reports readiness.

- [ ] **Step 3: Run backend tests**

Run:

```powershell
cd backend
python -m pytest -v
```

Expected: PASS.

- [ ] **Step 4: Run frontend build**

Run:

```powershell
cd frontend
pnpm build
```

Expected: PASS.

- [ ] **Step 5: Start local servers and inspect in browser**

Run backend on `localhost:8000` and frontend on `localhost:5173`. Open `http://localhost:5173` in the in-app browser. Verify the Today entry and immersive study page render without overlap.

- [ ] **Step 6: Commit**

```powershell
git add README.md backend frontend
git commit -m "docs: add local startup smoke test"
```

## Self-Review

Spec coverage:

- CSV import is covered by Task 2.
- Unified Prepare Job for next scope is covered by Task 3.
- Today session and Anki-like review flow are covered by Tasks 4 and 6.
- Ebbinghaus feedback is covered by Task 4.
- Full-book export readiness is covered by Task 5.
- Warm visual design is covered by Task 6.
- Local startup is covered by Task 7.

Known follow-up issues after MVP:

- Prepare `range` and `full_book` scopes with resumable progress.
- Real `.apkg` file generation in the complete export path if Task 5 only implements readiness plus minimal package generation.
- Oxford official API provider.
- Optional experimental Oxford HTML provider with rate limiting and local cache.
- AI IELTS example provider.
- OCR pipeline from the scanned PDF to `book_words.csv`.
