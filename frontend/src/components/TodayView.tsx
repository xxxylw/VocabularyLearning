import { useEffect, useState } from 'react';
import type { CheckInRecord } from '../checkins';
import { CheckInGrid } from './CheckInGrid';

type TodayViewProps = {
  onStart: (newWordTarget: number) => void;
  isLoading: boolean;
  newWordTarget: number;
  onNewWordTargetChange: (newWordTarget: number) => void;
  onPracticeSpelling?: () => void;
  canPracticeSpelling?: boolean;
  checkIns?: CheckInRecord[];
  error?: string | null;
  bookTitle?: string | null;
  // PRD ch.9: cover card data (总词数 > 学习进度读数). Optional while the
  // current book request is in flight or the backend predates ch.9.
  bookTotalWords?: number | null;
  bookLearnedWords?: number | null;
  // V3 P2: 登录账号邮箱（Today 首行右侧只读展示）。未登录/缺失时
  // 不渲染该元素，eyebrow 行布局自动回落为现状。
  userEmail?: string | null;
  onOpenBookShelf?: () => void;
  // V3-01 只读模式：订阅到期后学习动作锁定（书架/进度/统计仍可看）。
  readOnly?: boolean;
  onGoSubscribe?: () => void;
};

export function TodayView({
  onStart,
  isLoading,
  newWordTarget,
  onNewWordTargetChange,
  onPracticeSpelling,
  canPracticeSpelling = false,
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

  return (
    <section className="today-view" aria-labelledby="today-title">
      <div className="today-copy">
        <p className="today-eyebrow-row">
          <span className="eyebrow">Today</span>
          {userEmail ? (
            // V3 P2 首页 UI：登录邮箱只读展示（12px 灰阶、超长省略、
            // title 保留完整地址）。未登录时整个 span 不渲染。
            <span className="today-user-email" data-testid="today-user-email" title={userEmail}>
              {userEmail}
            </span>
          ) : null}
        </p>
        {bookTitle ? (
          <p className="book-title" data-testid="current-book-title">
            单词书：{bookTitle}
          </p>
        ) : null}
        <h1 id="today-title">Ready for today&apos;s cards</h1>
        <p className="today-note">
          A quiet desk, a short queue, and a focused pass through the words waiting for you.
        </p>
        <p className="today-motto">背下的每个词，都是去看世界的路</p>
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
        ) : (
          <button className="primary-action" type="button" onClick={handleStart} disabled={isLoading}>
            {isLoading ? 'Preparing cards' : 'Start today cards'}
          </button>
        )}
        {canPracticeSpelling && onPracticeSpelling && !readOnly ? (
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
