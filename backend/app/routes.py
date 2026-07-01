from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.models import (
    BookProgressResponse,
    DueReviewsResponse,
    ExportFullBookRequest,
    ExportFullBookResponse,
    ImportBookWordsResponse,
    PrepareJobRequest,
    PrepareJobResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    TodaySessionResponse,
    TodayStartRequest,
)
from app.repositories import get_book_progress, import_book_words_csv
from app.services import (
    ExportNotReadyError,
    ReviewConflictError,
    export_full_book_anki,
    get_anki_export_file_path,
    get_due_reviews,
    prepare_book_words,
    review_card,
    start_today_session,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "version": "0.1.0"}


@router.post("/book-words/import")
async def import_book_words(
    file: Annotated[UploadFile, File()],
    sourceName: Annotated[str, Form()] = "雅思词汇真经",
    replaceExisting: Annotated[bool, Form()] = False,
) -> ImportBookWordsResponse:
    try:
        return import_book_words_csv(
            await file.read(),
            source_name=sourceName,
            replace_existing=replaceExisting,
        )
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/book-words/progress")
def book_words_progress() -> BookProgressResponse:
    return get_book_progress()


@router.post("/prepare-jobs")
def create_prepare_job(request: PrepareJobRequest) -> PrepareJobResponse:
    try:
        return prepare_book_words(request)
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


@router.post("/export/anki/full-book")
def export_anki_full_book(
    request: ExportFullBookRequest,
) -> ExportFullBookResponse:
    try:
        return export_full_book_anki(request)
    except ExportNotReadyError as error:
        return JSONResponse(
            status_code=409,
            content=error.readiness.model_dump(),
        )


@router.get("/export/anki/files/{fileName}")
def download_anki_export(fileName: str) -> FileResponse:
    try:
        export_path = get_anki_export_file_path(fileName)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(
        export_path,
        media_type="application/octet-stream",
        filename=fileName,
    )
