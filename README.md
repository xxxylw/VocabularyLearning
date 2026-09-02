# VocabularyLearning

VocabularyLearning is an MVP for turning a vocabulary book word list into study cards. The backend imports `book_words.csv`, prepares deterministic fallback-enriched entries and cards, and schedules reviews. The frontend provides a local Today cards study flow. (The full-book Anki export was dropped from scope and has been removed.)

## Prerequisites

- Python >= 3.11
- pnpm

## One-Command Local Start

From the repository root:

```powershell
.\start.ps1
```

Open `http://127.0.0.1:5173`.

The script creates `backend/.venv` when needed, installs backend dependencies, installs frontend dependencies when `frontend/node_modules` is missing, starts the FastAPI backend on `http://127.0.0.1:8000`, and starts the Vite frontend on `http://127.0.0.1:5173`.

Logs and PID files are written to `tmp/`. To stop services started by the script:

```powershell
.\stop.ps1
```

## Backend Setup And Run

From the repository root:

```powershell
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

If you are using a different Python environment, install the backend dependencies in that environment and run:

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

The API is served at `http://localhost:8000`, with MVP routes mounted under `/api`.

## Frontend Setup And Run

From the repository root:

```powershell
cd frontend
pnpm install
pnpm dev
```

Open `http://localhost:5173`.

The Vite dev server proxies `/api` requests to `http://localhost:8000`, so the frontend can call backend routes without additional local configuration.

## Minimal Smoke Workflow

1. Import a small `book_words.csv` through `POST /api/book-words/import` or a future UI import flow.
2. Prepare the next words with `POST /api/prepare-jobs`.
3. Start Today cards in the frontend, or call `POST /api/study/today/start`.
4. Reveal a card and mark it `Known`.
5. Confirm the review schedule advances, for example by checking that the review response moves the card to the next stage and sets a later `nextDueAt`.

## Verification

- Backend: `cd backend; .venv/Scripts/python.exe -m pytest -v` -> 22 passed, 1 existing warning
- Frontend: `cd frontend; pnpm test` -> 19 passed
- Frontend: `cd frontend; pnpm build` -> passed
- Backend local server smoke: started on `http://127.0.0.1:8000`; `GET /api/health` returned `{ "ok": true, "version": "0.1.0" }`.
- Frontend local server smoke: running on `http://localhost:5173`.
- In-app browser inspection confirmed the Today entry page renders without visible overlap.
- Clicking `Start today cards` with an empty local database showed the empty state: `No cards are waiting today.`

## Current MVP Limitations

- Enrichment uses the local fallback provider only; Oxford, API, and AI providers are not connected yet.
- The full-book Anki `.apkg` export was removed (P1 scope decision); there is no export endpoint anymore.
- The PDF OCR pipeline is not implemented yet. Use `book_words.csv` as the source input for local smoke testing.
