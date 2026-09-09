import { useEffect, useState } from 'react';
import {
  ApiError,
  fetchCheckIns,
  fetchTodaySummary,
  getBookProgress,
  getCurrentBook,
  listBooks,
  lookupOxfordWord,
  lookupPronunciation,
  mergeCheckIns,
  reviewCard,
  startTodaySession,
  switchBook
} from './api';
import type { BookInfo, BookListItem, ReviewRating, StudyCard, TodaySummary } from './api';
import {
  buildCheckInRecord,
  loadCheckIns,
  markCheckInsMerged,
  mergedCheckInsFor,
  saveCheckIn
} from './checkins';
import { BookShelfView } from './components/BookShelfView';
import { SpellingSession } from './components/SpellingSession';
import { StudySession } from './components/StudySession';
import { TodayView } from './components/TodayView';

type Screen = 'today' | 'study' | 'spelling' | 'empty' | 'bookshelf';
type EmptyReason = 'no-cards' | 'no-book-words';

// PRD ch.8: day-level progress anchors from the Today session — kept
// across screens so both card mode and spelling mode resume the day
// queue's progress instead of restarting from 1.
type DayProgress = {
  totalCards: number;
  reviewedCards: number;
};

// V3-01 只读模式：readOnly=true 时 Today 的学习入口进入锁定态
// （书架/进度/统计照常浏览），后端仍以 403 拦截学习动作做双保险。
// 不传时行为与 v2 完全一致（既有集成测试直接渲染 App）。
export function App({ readOnly = false, onGoSubscribe, userEmail }: { readOnly?: boolean; onGoSubscribe?: () => void; userEmail?: string | null }) {
  const [screen, setScreen] = useState<Screen>('today');
  const [cards, setCards] = useState<StudyCard[]>([]);
  const [dayProgress, setDayProgress] = useState<DayProgress | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newWordTarget, setNewWordTarget] = useState(20);
  const [emptyReason, setEmptyReason] = useState<EmptyReason>('no-cards');
  const [checkIns, setCheckIns] = useState(() => loadCheckIns());
  const [lastCompletedCards, setLastCompletedCards] = useState<StudyCard[]>([]);
  // P0 2026-09-08 跨设备完成态：服务端是「今天是否完成」的唯一权威，
  // 单纯靠 lastCompletedCards 拿不到刷新/换设备后的状态。
  const [todaySummary, setTodaySummary] = useState<TodaySummary | null>(null);
  const [bookTitle, setBookTitle] = useState<string | null>(null);
  // PRD ch.9: cover card data + bookshelf state.
  const [bookTotalWords, setBookTotalWords] = useState<number | null>(null);
  const [bookLearnedWords, setBookLearnedWords] = useState<number | null>(null);
  const [bookFallbackNotice, setBookFallbackNotice] = useState<string | null>(null);
  const [bookshelfBooks, setBookshelfBooks] = useState<BookListItem[]>([]);
  const [isSwitching, setIsSwitching] = useState(false);
  const [bookshelfError, setBookshelfError] = useState<string | null>(null);

  useEffect(() => {
    refreshCurrentBook().catch(() => {
      // Book title is informational; keep the page usable when the
      // endpoint is unavailable (e.g. backend still starting up).
    });
    // P0 2026-09-08 跨设备完成态：mount 时拉一次今日 summary，
    // 决定 Today 页应显示「Start today cards」还是
    // 「再来一组 / 练习拼写」。后端 summary 不挂 study-entitlement
    // gate — 订阅到期时也能读，锁定态下也能看到「今天已完成」。
    refreshTodaySummary().catch(() => {
      // Summary 是 best-effort：拉取失败时退回到未完成态（仍显示
      // Start today cards），让用户至少能继续学习。
    });
    // P1 2026-09-08 打卡热点图服务端化：打卡记录以服务端为准
    // （跨设备一致），localStorage 只作离线回退。本地有历史且未对
    // 当前账号上报过时，先一次性 merge 上报。
    hydrateCheckIns().catch(() => {
      // 服务端不可用时保持 localStorage 快照（初始 state）。
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshTodaySummary() {
    const summary = await fetchTodaySummary();
    setTodaySummary(summary);
  }

  // P1 2026-09-08 打卡服务端化：服务端优先，localStorage 离线回退。
  // - 本地有记录且当前账号未上报过 → POST /api/check-ins/merge 一次
  //   （返回的合并列表直接整表替换），并记录 merge 标记（按账号）；
  // - 否则 GET /api/check-ins 覆盖初始的 localStorage 快照；
  // - 两条路径都失败 → 保留 localStorage 快照，页面照常可用。
  async function hydrateCheckIns() {
    const localRecords = loadCheckIns();
    const account = userEmail ?? 'anonymous';
    if (localRecords.length > 0 && !mergedCheckInsFor(account)) {
      try {
        const { checkIns } = await mergeCheckIns(localRecords);
        markCheckInsMerged(account);
        if (Array.isArray(checkIns)) {
          setCheckIns(checkIns);
        }
        return;
      } catch {
        // merge 失败（离线/服务端异常）→ 落到纯 GET 重试。
      }
    }
    await refreshCheckIns();
  }

  async function refreshCheckIns() {
    const { checkIns } = await fetchCheckIns();
    if (Array.isArray(checkIns)) {
      setCheckIns(checkIns);
    }
  }

  async function refreshCurrentBook() {
    // P1 2026-09-09 Today 封面卡主位缺失：/api/books/current 瞬时失败
    // （超时/5xx，线上部署窗口期出现过）时，若直接抛错，bookTitle/
    // totalWords/learnedWords 保持 null，Today 封面卡（含背完时间预估
    // 主位）整体不渲染。回退到书架列表端点 /api/books——列表项与
    // current 同形（含 totalWords/learnedWords）且带 isCurrent 标记，
    // QA 走查时书架辅位正常即证明该端点可用。
    let book: BookInfo;
    try {
      book = await getCurrentBook();
    } catch {
      const list = await listBooks();
      const current = list.books.find((item) => item.isCurrent);
      if (!current) {
        throw new Error('current book missing in book list');
      }
      book = current;
    }
    setBookTitle(book.title);
    setBookTotalWords(book.totalWords);
    setBookLearnedWords(book.learnedWords ?? null);
    setBookFallbackNotice(book.fallbackNotice ?? null);
  }

  async function openBookShelf() {
    setBookshelfError(null);
    try {
      const list = await listBooks();
      setBookshelfBooks(list.books);
      setScreen('bookshelf');
    } catch {
      setBookshelfError('Bookshelf could not be loaded. Please try again.');
    }
  }

  async function handleSwitchBook(bookId: string) {
    setIsSwitching(true);
    setBookshelfError(null);
    try {
      await switchBook(bookId);
      // PRD ch.9: after switching, Today follows the new book — refresh
      // the cover card data and return to Today with a clean slate (the
      // in-progress session ended; graded reviews stay persisted).
      await refreshCurrentBook();
      // 切书后当日 summary 不可信（换书的 completedCards 队列
      // 属于旧书），重置到未完成态，等 useEffect / 下一轮 mount
      // 重新拉新书的 summary。
      setTodaySummary(null);
      setCards([]);
      setDayProgress(null);
      setLastCompletedCards([]);
      setScreen('today');
      void refreshTodaySummary().catch(() => undefined);
    } catch {
      setBookshelfError('Switching the book failed. Please try again.');
    } finally {
      setIsSwitching(false);
    }
  }

  async function handleStart(target: number, extraNewWords = 0) {
    setIsLoading(true);
    setError(null);

    try {
      const session = await startTodaySession(target, extraNewWords);
      setCards(session.cards);
      setDayProgress({
        totalCards: session.totalCards,
        reviewedCards: session.reviewedCards ?? 0
      });
      if (session.cards.length > 0) {
        setScreen('study');
        return;
      }

      const progress = await getBookProgress();
      setEmptyReason(progress.totalWords === 0 ? 'no-book-words' : 'no-cards');
      setScreen('empty');
    } catch (error) {
      // QA P2: api 层错误已带 status/code，这里把 403 subscription_expired
      // 从普通加载失败里区分出来 —— 到期只读模式下给出续费引导，而不是
      // 一句含混的「加载失败」。
      if (
        error instanceof ApiError &&
        (error.code === 'subscription_expired' || error.status === 403)
      ) {
        setError('订阅已到期，学习功能已进入只读模式。续费后即可恢复学习。');
      } else {
        setError('Today cards could not be loaded. Please try again.');
      }
    } finally {
      setIsLoading(false);
    }
  }

  // P0 2026-09-08 「再来一组」: 当日队列背完后追加一组新卡加练。
  // 实现口径：
  //   - 数量 = 当前 newWordTarget 状态（不与 default quota 合并），
  //     用户在 Today 视图改的「New word target」输入框就是单组大小。
  //   - 一次点击 = 一次 extraNewWords delta，服务端 merge 路径按
  //     「quota_remaining + extra」计算当日队列追加量；多次点击
  //     累加（每次都是新 delta，无单日上限，限制仅剩词池余量）。
  //   - 跨日无残留：extra 不落库，次日新快照按复习记录重算配额。
  //   - 仅在 dayCompleted 时按钮可点；加练完成后回到 study 流程。
  async function handleAnotherGroup() {
    if (!todaySummary?.dayCompleted) {
      return;
    }
    await handleStart(newWordTarget, newWordTarget);
    // 后端 start 成功后服务端 summary 自动反映新增的 totalCards，
    // 但当天内是同一队列继续，不重置 dayCompleted；为保持 summary
    // 视图与服务端一致，start 之后再拉一次（best-effort）。
    void refreshTodaySummary().catch(() => undefined);
  }

  async function reviewWordCard(card: StudyCard, rating: ReviewRating) {
    const cardIds = card.cardIds.length > 0 ? card.cardIds : [card.cardId];
    await Promise.all(cardIds.map((cardId) => reviewCard(cardId, rating)));
  }

  function handleSessionComplete(completedCards: StudyCard[]) {
    setLastCompletedCards(completedCards);
    // P1 2026-09-08 打卡服务端化：localStorage 仍写入（离线快照 +
    // 首次上报源），先乐观更新；随后 best-effort 拉服务端派生列表
    // 整表替换（服务端是唯一权威，review 已落库，跨设备立即一致）。
    const updatedCheckIns = saveCheckIn(buildCheckInRecord(completedCards));
    setCheckIns(updatedCheckIns);
    // P0 2026-09-08：本地 lastCompletedCards 解决不了跨设备恢复，
    // 这里把服务端 summary 重新拉一次 — 完成后 dayCompleted 变 true，
    // 切到「再来一组 / 练习拼写」按钮组。
    void refreshTodaySummary().catch(() => undefined);
    void refreshCheckIns().catch(() => undefined);
  }

  // 拼写练习入口：优先用服务端 summary.completedCards（与当日队列
  // 顺序一致，跨设备可用），回退到 lastCompletedCards。
  function startSpellingPractice(spellingCards: StudyCard[]) {
    const cards = todaySummary?.completedCards?.length
      ? todaySummary.completedCards
      : spellingCards;
    setCards(cards);
    setLastCompletedCards(cards);
    setScreen('spelling');
  }

  function startReviewDueWords(reviewCards: StudyCard[]) {
    setCards(reviewCards);
    // Ad-hoc re-review subset: progress falls back to session-local
    // counting (PRD ch.8 only anchors Today-started sessions).
    setDayProgress(null);
    setScreen('study');
  }

  if (screen === 'study') {
    return (
      <StudySession
        cards={cards}
        totalCards={dayProgress?.totalCards}
        reviewedCards={dayProgress?.reviewedCards}
        onReview={reviewWordCard}
        onExit={() => setScreen('today')}
        onLookupWord={lookupOxfordWord}
        onLookupPronunciation={lookupPronunciation}
        onComplete={handleSessionComplete}
        onPracticeSpelling={startSpellingPractice}
        onReviewDueWords={startReviewDueWords}
      />
    );
  }

  if (screen === 'spelling') {
    return (
      <SpellingSession
        cards={cards}
        startIndex={dayProgress?.reviewedCards ?? 0}
        totalCount={dayProgress?.totalCards ?? cards.length}
        onExit={() => setScreen('today')}
        onLookupPronunciation={lookupPronunciation}
      />
    );
  }

  if (screen === 'bookshelf') {
    return (
      <main className="app-shell">
        <BookShelfView
          books={bookshelfBooks}
          onBack={() => setScreen('today')}
          onSwitch={handleSwitchBook}
          isSwitching={isSwitching}
          error={bookshelfError}
          notice={bookFallbackNotice}
          checkIns={checkIns}
          newWordTarget={newWordTarget}
        />
      </main>
    );
  }

  return (
    <main className="app-shell">
      <TodayView
        onStart={(target) => void handleStart(target)}
        onAnotherGroup={() => void handleAnotherGroup()}
        isLoading={isLoading}
        newWordTarget={newWordTarget}
        onNewWordTargetChange={setNewWordTarget}
        dayCompleted={todaySummary?.dayCompleted ?? false}
        canPracticeSpelling={
          (todaySummary?.completedCards?.length ?? 0) > 0 ||
          lastCompletedCards.length > 0
        }
        onPracticeSpelling={() => startSpellingPractice(lastCompletedCards)}
        checkIns={checkIns}
        error={error}
        bookTitle={bookTitle}
        bookTotalWords={bookTotalWords}
        bookLearnedWords={bookLearnedWords}
        onOpenBookShelf={() => void openBookShelf()}
        userEmail={userEmail}
        readOnly={readOnly}
        onGoSubscribe={onGoSubscribe}
      />
      {screen === 'empty' ? (
        <section className="empty-state" aria-live="polite">
          {emptyReason === 'no-book-words' ? (
            <>
              <p className="eyebrow">Book setup</p>
              <h2>No book words imported yet.</h2>
              <p>Import the book word list first, then Start today cards will prepare the next words in order.</p>
            </>
          ) : (
            <>
              <p className="eyebrow">All clear</p>
              <h2>Today&apos;s card queue is clear.</h2>
              <p>New words are done for the day. You can keep it light or run a spelling pass.</p>
              {lastCompletedCards.length > 0 ? (
                <div className="empty-actions">
                  <button
                    className="primary-action"
                    type="button"
                    onClick={() => startSpellingPractice(lastCompletedCards)}
                  >
                    Practice spelling now
                  </button>
                  <button className="ghost-button" type="button" onClick={() => setScreen('today')}>
                    Back home
                  </button>
                </div>
              ) : null}
            </>
          )}
        </section>
      ) : null}
    </main>
  );
}
