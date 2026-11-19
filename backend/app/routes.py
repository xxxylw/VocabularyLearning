from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import AuthContext, require_user
from app.subscription import SubscriptionError, require_study_entitlement
from app.books import DEFAULT_BOOK_ID
from app.lookup import lookup_oxford_word
from app.pronunciation import lookup_wiktionary_pronunciation
from app.version import APP_VERSION
from app.models import (
    BookListResponse,
    BookProgressResponse,
    BookSummaryResponse,
    CheckInsResponse,
    DueReviewsResponse,
    ImportBookWordsResponse,
    MergeCheckInsRequest,
    OxfordLookupResponse,
    PronunciationResponse,
    PrepareJobRequest,
    PrepareJobResponse,
    RepeatPoolReviewRequest,
    RepeatPoolReviewResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    SwitchBookRequest,
    TodaySessionResponse,
    TodayStartRequest,
    TodaySummaryResponse,
)
from app.repositories import get_book_progress, import_book_words_csv
from app.services import (
    ReviewConflictError,
    get_check_ins,
    get_current_book,
    get_due_reviews,
    get_today_summary,
    list_books,
    merge_check_ins,
    prepare_book_words,
    review_card,
    review_repeat_pool_card,
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


def _require_study_entitlement(context: AuthContext) -> None:
    """V3 只读模式：试用到期/未订阅用户的学习动作在服务端拦截。"""
    from app import auth

    user = auth.find_user_by_id(str(context.user_id)) if context.user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    try:
        require_study_entitlement(user)
    except SubscriptionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error


@router.post("/prepare-jobs")
def create_prepare_job(
    request: PrepareJobRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> PrepareJobResponse:
    _require_study_entitlement(context)
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
    _require_study_entitlement(context)
    return start_today_session(context.user_id, request)


# P0 2026-09-08 跨设备完成态恢复：read-only summary used to decide
# whether the Today page should render 「再来一组 / 练习拼写」instead
# of Start today cards. No study-entitlement gate — the data is
# observational and stays available even in read-only / 锁定态 so the
# user can still see what they finished today.
@router.get("/study/today/summary")
def get_today_summary_route(
    context: Annotated[AuthContext, Depends(require_user)],
    date: date | None = None,
) -> TodaySummaryResponse:
    return get_today_summary(context.user_id, date)


@router.post("/cards/{card_id}/reviews")
def create_card_review(
    card_id: str,
    request: ReviewCardRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> ReviewCardResponse:
    _require_study_entitlement(context)
    try:
        return review_card(context.user_id, card_id, request)
    except ReviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# 当日重复池（规格：会话层循环，Got it 一次才移除）。重复卡上的评分
# 只更新池状态 —— 不写 reviews、不触碰 SM-2，因此不存在 409 同卡
# 同日冲突；多义项以卡为粒度整卡操作，cardId 即卡 id。
@router.post("/study/today/repeat-pool/reviews")
def create_repeat_pool_review(
    request: RepeatPoolReviewRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> RepeatPoolReviewResponse:
    _require_study_entitlement(context)
    try:
        return review_repeat_pool_card(
            context.user_id, request.cardId, request.rating
        )
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


# P1 2026-09-08 打卡热点图服务端化：打卡记录改为从 reviews 按 study_date
# 聚合派生（方案一：零新增表）。与 /study/today/summary 一样属观察性数据，
# 不挂 study-entitlement gate —— 到期锁定态也能看到自己的打卡历史。
@router.get("/check-ins")
def check_ins_list(
    context: Annotated[AuthContext, Depends(require_user)],
) -> CheckInsResponse:
    return get_check_ins(context.user_id)


# 首次启动把浏览器 localStorage 的历史打卡一次性上报合并；返回合并后
# 的完整列表（与 GET 同形）。
@router.post("/check-ins/merge")
def check_ins_merge(
    request: MergeCheckInsRequest,
    context: Annotated[AuthContext, Depends(require_user)],
) -> CheckInsResponse:
    try:
        return merge_check_ins(context.user_id, request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/lookup/oxford")
def lookup_oxford(
    word: str,
    context: Annotated[AuthContext, Depends(require_user)],
) -> OxfordLookupResponse:
    try:
        result = lookup_oxford_word(word)
    except ValueError as error:
        # café / résumé 这类带重音字符的 400 是明示的字符集设计约束，
        # 客户端本地 fallback（QA 第三轮确认不改）。
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError:
        # 兜底：lookup_oxford_word 已把上游 OSError 归一为空数据；若
        # 未来调用路径变化再抛 OSError，这里仍按 B2 诚实降级返回
        # 200 + 空数据，而不是 5xx。
        result = OxfordLookupResponse(word=word, sourceUrl="", senses=[])

    # B2 诚实降级：查不到释义 = 200 + 空 senses，不再 404（QA 第三轮：
    # abeyance 404、aboveboard 等词 502）。客户端只区分「400 字符集
    # 不支持」与「200 空/有数据」两种形态。
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
