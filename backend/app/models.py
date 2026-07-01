from pydantic import BaseModel


class ImportBookWordsResponse(BaseModel):
    sourceId: str
    imported: int
    skipped: int
    needsReview: int


class BookProgressResponse(BaseModel):
    totalWords: int
    nextSequenceIndex: int | None


class PrepareJobRequest(BaseModel):
    scope: str
    count: int | None = None
    maxSensesPerWord: int = 5
    overwriteExisting: bool = False


class PrepareJobResponse(BaseModel):
    jobId: str
    status: str
    totalWords: int
    processedWords: int
    readyCards: int
    needsReview: int
    failedWords: list[str]
