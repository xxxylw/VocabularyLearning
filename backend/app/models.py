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


class BookSummaryResponse(BaseModel):
    id: str
    title: str
    description: str | None
    source: str | None
    createdAt: str
    updatedAt: str
    totalWords: int


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


DefinitionSource = Literal[
    "manual",
    "oxford_api",
    "open_api",
    "imported",
    "ai",
    "experimental_html",
    "fallback",
]

ExampleSource = Literal[
    "manual",
    "oxford_api",
    "ai",
    "template",
    "imported",
    "experimental_html",
    "fallback",
]


class StudySenseResponse(BaseModel):
    cardId: str
    partOfSpeech: str
    senseLabel: str
    definition: str
    definitionSource: DefinitionSource
    examples: list[StudyExampleResponse]
    chineseNote: str | None


class StudyCardResponse(BaseModel):
    cardId: str
    cardIds: list[str]
    word: str
    partOfSpeech: str
    senseLabel: str
    definition: str
    definitionSource: DefinitionSource
    examples: list[StudyExampleResponse]
    chineseNote: str | None
    senses: list[StudySenseResponse]
    status: str
    stage: int
    dueAt: Date
    queueType: Literal["new", "review"]
    degraded: bool = False
    # 1-based position in the day's queue snapshot (PRD ch.8); only set
    # when the card comes from today's queue read.
    queuePosition: int | None = None


class TodaySessionResponse(BaseModel):
    totalCards: int
    cards: list[StudyCardResponse]
    # Number of entries in the day's queue snapshot already reviewed on
    # the study date (PRD ch.8 rule 6: numerator offset for the progress
    # bar so it never restarts from 1 after re-entering Today).
    reviewedCards: int = 0


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


class OxfordLookupSenseResponse(BaseModel):
    partOfSpeech: str
    definition: str
    example: str | None = None


class OxfordLookupResponse(BaseModel):
    word: str
    sourceUrl: str
    senses: list[OxfordLookupSenseResponse]


class PronunciationResponse(BaseModel):
    word: str
    ipa: str | None = None
    ipaUk: str | None = None
    ipaUs: str | None = None
    audioUrl: str | None = None
    sourceUrl: str
    audioSourceUrl: str | None = None
    attribution: str | None = None
    license: str | None = None
    licenseUrl: str | None = None
    status: Literal["ready", "unavailable"]

