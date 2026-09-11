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
    # V3-09 (2026-09-11 DP-1 拍板): 已充值灰态统一布尔位 — 后端按
    # 「最新行 source ∈ 付费渠道 且 active 且未到期」UTC 判定，前端只
    # 渲染不推导；trialing / expired / canceled 恒 False，super 恒 False。
    hasActivePaidSubscription: bool = False


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
    # P0 2026-09-08 「再来一组」：当日队列背完后追加一组新卡加练的
    # 数量，超出当日默认新词量。只作用于本次调用、不落库 — 跨日
    # 的每日快照按各日复习记录重新计算配额，加练无跨日残留。
    extraNewWords: int = Field(default=0, ge=0)


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
    # 当日重复池：当日队列 new 卡评 New 后间隔 3 张重新出现的重复卡
    # 标记（服务端待学流注入 / 前端本地插入的副本都带它）。重复卡上
    # 的评分走池端点（只更新池状态，不写 reviews、不改 SM-2）。
    isRepeat: bool = False


class TodaySessionResponse(BaseModel):
    totalCards: int
    cards: list[StudyCardResponse]
    # Number of entries in the day's queue snapshot already reviewed on
    # the study date (PRD ch.8 rule 6: numerator offset for the progress
    # bar so it never restarts from 1 after re-entering Today).
    reviewedCards: int = 0


# P0 2026-09-08 跨设备完成态恢复：read-only summary of today's queue so
# the frontend can render the 「再来一组 / 练习拼写」buttons on page
# load (and on cross-device refresh) without a stateful start call.
# `dayCompleted` is the source of truth for swapping the Start button
# out; `completedCards` is the spelled-practice card list, ordered by
# the day's queue position so spelling keeps the same left-to-right
# learning order as the card-mode session.
class TodaySummaryResponse(BaseModel):
    studyDate: Date
    totalCards: int
    reviewedCards: int
    # 当日队列已生成且全部完成（totalCards > 0 且无待复习条目）。
    dayCompleted: bool = False
    # 当日队列中已复习的卡（队列顺序），供拼写练习跨设备恢复。
    completedCards: list[StudyCardResponse] = Field(default_factory=list)


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


# 当日重复池（会话层循环）：重复卡上的三按钮只更新池状态。
# - known → cleared：移出池，当日不再出现（Got it 一次才移除）；
# - uncertain / unknown → repeat_count +1、defer 重置（间隔 3 张再次
#   后移），达 3 次上限置 capped 自动移出（SM-2 次日重学兜底）；
# - 不写 reviews、不改 EF / 间隔 / due_at，与同卡同日唯一评分（409）
#   零冲突。
class RepeatPoolReviewRequest(BaseModel):
    cardId: str
    rating: Literal["known", "uncertain", "unknown"]


class RepeatPoolReviewResponse(BaseModel):
    cardId: str
    status: Literal["pending", "cleared", "capped"]
    # 数据面（规格规则 10）：该卡当日已重新出现的次数。
    repeatCount: int


# ---------------------------------------------------------------------------
# P1 2026-09-08 打卡热点图服务端化（task 7683154325467565322）。
# 打卡记录原先只存浏览器 localStorage（key=vocabulary-learning-check-ins），
# 跨设备不同步、且只覆盖前端完成回调路径。现改为从 reviews 按
# study_date 聚合派生（服务端是「完成判定」的唯一权威），本地历史
# 记录通过 merge 端点一次性上报。
# ---------------------------------------------------------------------------

# 单日打卡记录：与前端 checkins.ts 的 CheckInRecord 同形。
class CheckInDayPayload(BaseModel):
    date: Date
    completedCards: int = Field(default=0, ge=0)
    newCards: int = Field(default=0, ge=0)
    reviewCards: int = Field(default=0, ge=0)
    # 该日最后一次完成时刻（ISO 字符串），派生记录取 max(reviewed_at)。
    completedAt: str = ""


class CheckInsResponse(BaseModel):
    checkIns: list[CheckInDayPayload] = Field(default_factory=list)


# 首次启动时浏览器把 localStorage 里的历史打卡一次性上报合并。
class MergeCheckInsRequest(BaseModel):
    checkIns: list[CheckInDayPayload] = Field(default_factory=list)


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
