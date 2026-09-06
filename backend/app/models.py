from __future__ import annotations

from datetime import date as Date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# v2 cloud edition (batch 1) — account & auth models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str


class RegisterResponse(BaseModel):
    email: str
    message: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    emailVerified: bool
    isSuper: bool


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str


class EmailOnlyRequest(BaseModel):
    email: str


class EmailStatusResponse(BaseModel):
    verified: bool


# C-01a: verification is submitted as email + 6-digit code (the code
# arrives in the email body; no link is sent anymore).
class VerifyEmailCodeRequest(BaseModel):
    email: str
    code: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    newPassword: str


class TokenEmailResponse(BaseModel):
    email: str


# v3 (V3-02): the plan response became four configuration-driven tiers
# (月付 / 续费优惠 / 半年 / 一年) + server-judged renew eligibility.
# Every timestamp is a UTC ISO string. For super accounts ``plan`` is
# the synthesized "super" view with ``expiresAt=None`` (permanent) and
# no subscriptions row exists.
class SubscriptionTierResponse(BaseModel):
    plan: str
    label: str
    priceCents: int
    currency: str
    durationDays: int


class SubscriptionPlansResponse(BaseModel):
    plans: list[SubscriptionTierResponse]
    currency: str
    trialDays: int
    renewGraceDays: int
    renewEligible: bool
    paymentEnabled: bool
    # v3 官方通道（2026-09-06 拍板）：逐渠道可用性，收银台据此点亮
    # 微信扫码 / 支付宝跳转按钮。paymentEnabled = 任一渠道可用。
    channels: dict[str, bool]


class SubscriptionStatusResponse(BaseModel):
    subscribed: bool
    plan: str | None
    status: str | None
    startedAt: str | None
    expiresAt: str | None
    autoRenew: bool | None
    source: str | None
    # v3 extensions (V3-01/V3-02): trial countdown, read-only flag,
    # renew-eligibility snapshot (server-judged) and the 续费提醒开关
    # (management-page's only user-controlled switch, default on).
    trialDaysLeft: int | None = None
    readOnly: bool = False
    renewEligible: bool = False
    renewDeadline: str | None = None
    renewReminder: bool | None = None


# v3 (V3-03): payment order models. amountCents is the snapshotted
# payable amount (回调金额必须一致才确认入账); expiresAt is the
# checkout countdown basis (下单时刻 + 15 分钟, 超时自动关单).
# channel: 'wechat'（Native 扫码）或 'alipay'（官方收银台跳转）。
class CreateOrderRequest(BaseModel):
    plan: str
    channel: str


class OrderResponse(BaseModel):
    outTradeNo: str
    plan: str
    amountCents: int
    currency: str
    status: str
    channel: str
    payUrl: str | None
    payQrUrl: str | None
    createdAt: str
    paidAt: str | None
    expiresAt: str | None


class LatestOrderResponse(BaseModel):
    order: OrderResponse | None
    subscription: SubscriptionStatusResponse


class RenewReminderRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# v1 study models
# ---------------------------------------------------------------------------


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
    # PRD ch.9: per-book progress aggregates (learned / mastered counts)
    # used by the Today cover card and the bookshelf list.
    learnedWords: int = 0
    masteredWords: int = 0
    # Set when the current-book pointer referenced a missing book and the
    # backend fell back to the default book (PRD ch.9 异常兜底).
    fallbackNotice: str | None = None


class BookListItemResponse(BookSummaryResponse):
    isCurrent: bool = False


class BookListResponse(BaseModel):
    books: list[BookListItemResponse]


class SwitchBookRequest(BaseModel):
    bookId: str


class PrepareJobRequest(BaseModel):
    scope: str
    count: int | None = Field(default=None, gt=0)
    maxSensesPerWord: int = 5
    overwriteExisting: bool = False
    # PRD ch.10: batch enrichment jobs may target a specific book (e.g.
    # preparing the whole 考研英语红宝书 import) without rewriting the
    # current-book pointer. Defaults to the current book (PRD ch.9).
    bookId: str | None = None


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
