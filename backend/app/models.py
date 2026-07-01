from pydantic import BaseModel


class ImportBookWordsResponse(BaseModel):
    sourceId: str
    imported: int
    skipped: int
    needsReview: int


class BookProgressResponse(BaseModel):
    totalWords: int
    nextSequenceIndex: int | None
