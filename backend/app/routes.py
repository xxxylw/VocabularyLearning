from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import require_user
from app.books import DEFAULT_BOOK_ID
from app.lookup import lookup_oxford_word
from app.pronunciation import lookup_wiktionary_pronunciation
from app.version import APP_VERSION
from app.models import (
    BookListResponse,
    BookProgressResponse,
    BookSummaryResponse,
    DueReviewsResponse,
    ImportBookWordsResponse,
    OxfordLookupResponse,
    PronunciationResponse,
    PrepareJobRequest,
    PrepareJobResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    SwitchBookRequest,
    TodaySessionResponse,
    TodayStartRequest,
)
from app.repositories import get_book_progress, import_book_words_csv
from app.services import (
    ReviewConflictError,
    get_current_book,
    get_due_reviews,
    list_books,
    prepare_book_words,
    review_card,
    start_today_session,
    switch_current_book,
)

# v2 cloud edition: every study endpoint requires a valid session
# (see C-02 in the batch-1 brief). /api/health is served by main.py so
# the launcher can keep polling it without credentials. Set
# VOCAB_REQUIRE_AUTH=0 to disable the guard (tests / v1.1 desktop
# compat only).
router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/book-words/import")
async def import_book_words(
    file: Annotated[UploadFile, File()],
    sourceName: Annotated[str, Form()] = "雅思词汇真经",
    replaceExisting: Annotated[bool, Form()] = False,
    bookId: Annotated[str, Form()] = "",
    bookTitle: Annotated[str, Form()] = "",
    bookDescription: Annotated[str, Form()] = "",
) -> ImportBookWordsResponse:
    try:
        return import_book_words_csv(
            await file.read(),
            source_name=sourceName,
            replace_existing=replaceExisting,
            book_id=bookId or DEFAULT_BOOK_ID,
            book_title=bookTitle or None,
            book_description=bookDescription or None,
        )
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/books/current")
def books_current() -> BookSummaryResponse:
    return get_current_book()


@router.get("/books")
def books_list() -> BookListResponse:
    return list_books()


@router.put("/books/current")
def books_switch_current(request: SwitchBookRequest) -> BookSummaryResponse:
    try:
        return switch_current_book(request.bookId)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/book-words/progress")
def book_words_progress() -> BookProgressResponse:
    return get_book_progress()


@router.post("/prepare-jobs")
def create_prepare_job(request: PrepareJobRequest) -> PrepareJobResponse:
    try:
        return prepare_book_words(request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/study/today/start")
def create_today_session(request: TodayStartRequest) -> TodaySessionResponse:
    return start_today_session(request)


@router.post("/cards/{card_id}/reviews")
def create_card_review(
    card_id: str,
    request: ReviewCardRequest,
) -> ReviewCardResponse:
    try:
        return review_card(card_id, request)
    except ReviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/reviews/due")
def reviews_due(date: date) -> DueReviewsResponse:
    return get_due_reviews(date)


@router.get("/lookup/oxford")
def lookup_oxford(word: str) -> OxfordLookupResponse:
    try:
        result = lookup_oxford_word(word)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=502, detail="Oxford lookup is temporarily unavailable") from error

    if not result.senses:
        raise HTTPException(status_code=404, detail=f"No Oxford definitions found for '{word}'")

    return result


@router.get("/pronunciations/{word}")
def get_pronunciation(word: str) -> PronunciationResponse:
    try:
        return PronunciationResponse(**lookup_wiktionary_pronunciation(word))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=502, detail="Pronunciation lookup is temporarily unavailable") from error


# Public health endpoint (no auth) — see module docstring.
def health() -> dict[str, object]:
    return {"ok": True, "version": APP_VERSION}
