import { useEffect, useState } from 'react';
import type { CheckInRecord } from '../checkins';
import { estimateFinishDays, formatFinishEstimate } from '../estimate';
import { CheckInGrid } from './CheckInGrid';

type TodayViewProps = {
  onStart: (newWordTarget: number) => void;
  // P0 2026-09-08 「再来一组」: 当日队列完成后追加一组新卡加练。
  onAnotherGroup?: () => void;
  isLoading: boolean;
  newWordTarget: number;
  onNewWordTargetChange: (newWordTarget: number) => void;
  onPracticeSpelling?: () => void;
  canPracticeSpelling?: boolean;
  // P0 2026-09-08 跨设备完成态：服务端是「今天是否完成」的唯一权威。
  // 当 true 时不再显示 Start today cards，改为「再来一组 + 练习拼写」。
  dayCompleted?: boolean;
  checkIns?: CheckInRecord[];
  error?: string | null;
  bookTitle?: string | null;
  // PRD ch.9: cover card data (总词数 > 学习进度读数). Optional while the
  // current book request is in flight or the backend predates ch.9.
  bookTotalWords?: number | null;
  bookLearnedWords?: number | null;
  // V3 P2 → 2026-09-08 调整：登录邮箱以「@邮箱 + today-note 英文句」同行
  // 前缀展示。未登录/缺失时不渲染前缀，句子照常显示。
  userEmail?: string | null;
  onOpenBookShelf?: () => void;
  // V3-01 只读模式：订阅到期后学习动作锁定（书架/进度/统计仍可看）。
  readOnly?: boolean;
  onGoSubscribe?: () => void;
};

export function TodayView({
  onStart,
  onAnotherGroup,
  isLoading,
  newWordTarget,
  onNewWordTargetChange,
  onPracticeSpelling,
  canPracticeSpelling = false,
  dayCompleted = false,
  checkIns = [],
  error,
  bookTitle,
  bookTotalWords,
  bookLearnedWords,
  userEmail,
  onOpenBookShelf,
  readOnly = false,
  onGoSubscribe
}: TodayViewProps) {
  const [targetDraft, setTargetDraft] = useState(String(newWordTarget));

  useEffect(() => {
    setTargetDraft(String(newWordTarget));
  }, [newWordTarget]);

  function handleTargetChange(value: string) {
    setTargetDraft(value);

    const nextTarget = Number.parseInt(value, 10);

    if (Number.isNaN(nextTarget)) {
      return;
    }

    onNewWordTargetChange(Math.min(200, Math.max(1, nextTarget)));
  }

  function handleStart() {
    const nextTarget = Number.parseInt(targetDraft, 10);
    onStart(Number.isNaN(nextTarget) ? newWordTarget : Math.min(200, Math.max(1, nextTarget)));
  }

  // 需求 A「背完时间预估」：主位在当前书封面卡进度行下方（DP-A3）。
  // 数据全部来自已有状态（bookTotalWords / bookLearnedWords / checkIns /
  // newWordTarget），目标改动后这里即时重算；速度不足 3 天样本回退目标值。
  const finishEstimate =
    typeof bookTotalWords === 'number' && typeof bookLearnedWords === 'number'
      ? estimateFinishDays(bookTotalWords, bookLearnedWords, checkIns, newWordTarget)
      : { kind: 'unavailable' as const };
  const finishEstimateText =
    finishEstimate.kind === 'unavailable' ? '' : formatFinishEstimate(finishEstimate);

  return (
    <section className="today-view" aria-labelledby="today-title">
      <div className="today-copy">
        <p className="today-eyebrow-row">
          <span className="eyebrow">Today</span>
        </p>
        {bookTitle ? (
          <p className="book-title" data-testid="current-book-title">
            单词书：{bookTitle}
          </p>
        ) : null}
        <h1 id="today-title">Ready for today&apos;s cards</h1>
        <p className="today-note">
          {/* 2026-09-08 需求：@邮箱前缀 + 英文句同行展示；中文释义句已移除。
              未登录/邮箱缺失时只渲染句子本身。 */}
          {userEmail ? (
            <span className="today-note-email" data-testid="today-user-email" title={userEmail}>
              @{userEmail}
            </span>
          ) : null}
          {userEmail ? ' ' : null}
          A quiet desk, a short queue, and a focused pass through the words waiting for you.
        </p>
        {bookTitle && onOpenBookShelf ? (
          // PRD ch.9: programmatic cover card (pure CSS spine style) —
          // 书名 > 总词数 > 学习进度读数. The whole card is the entry to
          // the bookshelf; the long title stays available via the title
          // attribute when it truncates.
          <button
            type="button"
            className="book-cover-card"
            data-testid="book-cover-card"
            onClick={onOpenBookShelf}
            aria-label={`查看单词书书架，当前书《${bookTitle}》`}
          >
            <span className="book-cover-spine" aria-hidden="true" />
            <span className="book-cover-body">
              <span className="book-cover-title" title={bookTitle}>
                {bookTitle}
              </span>
              <span className="book-cover-meta">
                {typeof bookTotalWords === 'number' ? `${bookTotalWords} 词` : null}
              </span>
              <span className="book-cover-progress">
                {typeof bookLearnedWords === 'number' && typeof bookTotalWords === 'number'
                  ? `已学 ${bookLearnedWords} / ${bookTotalWords}`
                  : null}
              </span>
              {finishEstimateText ? (
                <span className="book-cover-estimate" data-testid="book-cover-estimate">
                  {finishEstimateText}
                </span>
              ) : null}
            </span>
          </button>
        ) : null}
      </div>

      <div className="desk-panel" aria-label="Study desk summary">
        {readOnly ? (
          // 只读锁定态：置灰 + 锁图标（后端同样拦截 403，双重保险）。
          <div className="stat-row today-locked" data-testid="today-locked">
            <span>Mode</span>
            <strong>
              <span className="today-locked-lock" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <rect x="3" y="7" width="10" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
                  <path d="M5.5 7V5a2.5 2.5 0 015 0v2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
              </span>
              学习功能已锁定
            </strong>
          </div>
        ) : dayCompleted ? (
          // P0 2026-09-08 完成态：服务端 summary 是唯一权威，
          // 跨设备刷新后仍正确显示。把 Start today cards 换成
          // 「再来一组」追加一组新卡、「练习拼写」进入拼写视图。
          <>
            <div className="stat-row" data-testid="today-day-completed">
              <span>Today</span>
              <strong>今日卡片已背完 🎉</strong>
            </div>
            <button
              className="primary-action"
              type="button"
              onClick={onAnotherGroup}
              disabled={isLoading || !onAnotherGroup}
              data-testid="another-group"
            >
              {isLoading ? 'Preparing cards' : '再来一组'}
            </button>
            {canPracticeSpelling && onPracticeSpelling ? (
              <button
                className="secondary-action"
                type="button"
                onClick={onPracticeSpelling}
                disabled={isLoading}
                data-testid="practice-spelling-completed"
              >
                练习拼写
              </button>
            ) : null}
          </>
        ) : (
          <div className="stat-row">
            <label htmlFor="new-word-target">New word target</label>
            <input
              id="new-word-target"
              className="target-input"
              type="number"
              min="1"
              max="200"
              step="1"
              value={targetDraft}
              onChange={(event) => handleTargetChange(event.target.value)}
              disabled={isLoading}
            />
          </div>
        )}
        <div className="stat-row">
          <span>Rhythm</span>
          <strong>Reveal, rate, continue</strong>
        </div>
        {readOnly ? (
          <button
            className="primary-action today-locked-cta"
            type="button"
            onClick={onGoSubscribe}
          >
            续费解锁学习
          </button>
        ) : dayCompleted ? null : (
          <button className="primary-action" type="button" onClick={handleStart} disabled={isLoading}>
            {isLoading ? 'Preparing cards' : 'Start today cards'}
          </button>
        )}
        {!dayCompleted && canPracticeSpelling && onPracticeSpelling && !readOnly ? (
          <button className="secondary-action" type="button" onClick={onPracticeSpelling} disabled={isLoading}>
            Practice spelling
          </button>
        ) : null}
        {error ? <p className="inline-error">{error}</p> : null}
      </div>

      <CheckInGrid checkIns={checkIns} />
    </section>
  );
}
