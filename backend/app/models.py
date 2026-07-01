from __future__ import annotations

from datetime import date as Date, datetime
from typing import Literal

from pydantic import BaseModel, Field


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
    count: int | None = Field(default=None, gt=0)
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


class TodayStartRequest(BaseModel):
    date: Date | None = None
    dailyNewWordTarget: int = Field(default=20, gt=0)


class StudyExampleResponse(BaseModel):
    exampleId: str
    sentence: str
    isPrimary: bool


class StudyCardResponse(BaseModel):
    cardId: str
    word: str
    partOfSpeech: str
    senseLabel: str
    definition: str
    examples: list[StudyExampleResponse]
    chineseNote: str | None
    status: str
    stage: int
    dueAt: Date
    queueType: Literal["new", "review"]


class TodaySessionResponse(BaseModel):
    totalCards: int
    cards: list[StudyCardResponse]


class ReviewCardRequest(BaseModel):
    rating: Literal["known", "uncertain", "unknown"]
    reviewedAt: datetime
    reviewedDate: Date | None = None


class ReviewCardResponse(BaseModel):
    cardId: str
    rating: Literal["known", "uncertain", "unknown"]
    previousStage: int
    nextStage: int
    nextDueAt: Date
    status: str


class DueReviewsResponse(BaseModel):
    date: Date
    total: int
    cards: list[StudyCardResponse]


class ExportFullBookRequest(BaseModel):
    deckName: str
    includeChineseNote: bool = True


class ExportReadinessError(BaseModel):
    totalWords: int
    preparedWords: int
    missingWords: int


class ExportFullBookResponse(BaseModel):
    downloadUrl: str
    cardCount: int
