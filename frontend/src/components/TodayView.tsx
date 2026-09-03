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
  onOpenBookShelf?: () => void;
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
  onOpenBookShelf
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
        <p className="eyebrow">Today</p>
        {bookTitle ? (
          <p className="book-title" data-testid="current-book-title">
            单词书：{bookTitle}
          </p>
        ) : null}
        <h1 id="today-title">Ready for today&apos;s cards</h1>
        <p className="today-note">
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
            </span>
          </button>
        ) : null}
      </div>

      <div className="desk-panel" aria-label="Study desk summary">
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
        <div className="stat-row">
          <span>Mode</span>
          <strong>Today cards</strong>
        </div>
        <div className="stat-row">
          <span>Rhythm</span>
          <strong>Reveal, rate, continue</strong>
        </div>
        <button className="primary-action" type="button" onClick={handleStart} disabled={isLoading}>
          {isLoading ? 'Preparing cards' : 'Start today cards'}
        </button>
        {canPracticeSpelling && onPracticeSpelling ? (
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
