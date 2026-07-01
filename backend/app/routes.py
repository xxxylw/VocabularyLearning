from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models import BookProgressResponse, ImportBookWordsResponse
from app.repositories import get_book_progress, import_book_words_csv

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
