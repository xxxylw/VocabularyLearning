from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import AuthContext, require_user
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
# the launcher can keep polling it without credentials. Batch 2 removed
# the VOCAB_REQUIRE_AUTH=0 fallback: the Bearer token is always required.
router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/book-words/import")
async def import_book_words(
    context: Annotated[AuthContext, Depends(require_user)],
    file: Annotated[UploadFile, File()],
    sourceName: Annotated[str, Form()] = "雅思词汇真经",
    replaceExisting: Annotated[bool, Form()] = False,
    bookId: Annotated[str, Form()] = "",
    bookTitle: Annotated[str, Form()] = "",
    bookDescription: Annotated[str, Form()] = "",
) -> ImportBookWordsResponse:
    # Shared content layer: only the super account manages book imports
    # so a regular user cannot replace the shared word list.
    if not context.is_super:
        raise HTTPException(status_code=403, detail="Only the super account can import book words")
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
def books_current(
    context: Annotated[AuthContext, Depends(require_user)],
) -> BookSummaryResponse:
    return get_current_book(context.user_id)


@router.get("/books")
def books_list(
    context: Annotated[AuthContext, Depends(require_user)],
) -> BookListResponse:
    return list_books(context.user_id)


@router.put("/books/current")
def books_switch_current(
    request: SwitchBookRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> BookSummaryResponse:
    try:
        return switch_current_book(context.user_id, request.bookId)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/book-words/progress")
def book_words_progress(
    context: Annotated[AuthContext, Depends(require_user)],
) -> BookProgressResponse:
    return get_book_progress(context.user_id)


@router.post("/prepare-jobs")
def create_prepare_job(
    request: PrepareJobRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> PrepareJobResponse:
    try:
        return prepare_book_words(
            context.user_id, request, is_super=context.is_super
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post("/study/today/start")
def create_today_session(
    request: TodayStartRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> TodaySessionResponse:
    return start_today_session(context.user_id, request)


@router.post("/cards/{card_id}/reviews")
def create_card_review(
    card_id: str,
    request: ReviewCardRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> ReviewCardResponse:
    try:
        return review_card(context.user_id, card_id, request)
    except ReviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/reviews/due")
def reviews_due(
    date: date,
    context: Annotated[AuthContext, Depends(require_user)],
) -> DueReviewsResponse:
    return get_due_reviews(context.user_id, date)


@router.get("/lookup/oxford")
def lookup_oxford(
    word: str,
    context: Annotated[AuthContext, Depends(require_user)],
) -> OxfordLookupResponse:
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
def get_pronunciation(
    word: str,
    context: Annotated[AuthContext, Depends(require_user)],
) -> PronunciationResponse:
    try:
        return PronunciationResponse(**lookup_wiktionary_pronunciation(word))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=502, detail="Pronunciation lookup is temporarily unavailable") from error


# Public health endpoint (no auth) — see module docstring.
def health() -> dict[str, object]:
    return {"ok": True, "version": APP_VERSION}
