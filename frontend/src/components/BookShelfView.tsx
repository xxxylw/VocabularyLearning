import { useState } from 'react';
import type { BookListItem } from '../api';

// 2026-09-11 需求变更：书架/选书页不再展示「还有多久背完」预估（用户口径：
// 只在主页 Today 封面卡标注）。后端预估数据/API 不动，TodayView 保留原样。

// PRD ch.10: the second built-in book gets a red programmatic cover so the
// two shelf entries are visually distinct (纯 CSS，无图片，零版权风险).
// Keep in sync with backend/app/books.py RED_BOOK_ID.
export const RED_BOOK_ID = 'kaoyan-hongbaoshu-2027';

// 2026-09-12 (PRD ch.15 + ch.16): covers for the five new built-in books
// (托福词汇真经 / 六级词汇闪过 / 四级词汇闪过 / 如鱼得水记单词·专四 /
// 如鱼得水记单词·专八) — programmatic palettes, distinct from each other
// and from the existing green (default) / red (红宝书) covers. Keep in sync
// with backend/app/books.py.
const BOOK_COVER_CLASSES: Record<string, string> = {
  [RED_BOOK_ID]: 'bookshelf-cover--red',
  'toefl-zhenjing-2026': 'bookshelf-cover--blue',
  'cet6-shanguo-2026': 'bookshelf-cover--purple',
  'cet4-shanguo-2026': 'bookshelf-cover--teal',
  'tem4-ruyudeshui-2026': 'bookshelf-cover--amber',
  'tem8-ruyudeshui-2026': 'bookshelf-cover--slate'
};

function coverClassFor(bookId: string): string {
  return BOOK_COVER_CLASSES[bookId] ?? '';
}

type BookShelfViewProps = {
  books: BookListItem[];
  onBack: () => void;
  // PRD ch.9: switching is a low-frequency, high-impact action (the whole
  // Today queue is replaced), so a confirm dialog always precedes it.
  onSwitch: (bookId: string) => Promise<void> | void;
  isSwitching?: boolean;
  error?: string | null;
  // Fallback notice from GET /api/books/current when the pointer
  // referenced a missing book and the default book took over.
  notice?: string | null;
};

export function BookShelfView({
  books,
  onBack,
  onSwitch,
  isSwitching = false,
  error,
  notice
}: BookShelfViewProps) {
  const [confirmTarget, setConfirmTarget] = useState<BookListItem | null>(null);

  function handleBookClick(book: BookListItem) {
    if (book.isCurrent || book.totalWords === 0) {
      return;
    }
    setConfirmTarget(book);
  }

  async function handleConfirmSwitch() {
    if (!confirmTarget) {
      return;
    }
    const target = confirmTarget;
    setConfirmTarget(null);
    await onSwitch(target.id);
  }

  return (
    <section className="bookshelf-view" aria-labelledby="bookshelf-title">
      <header className="bookshelf-header">
        <div>
          <p className="eyebrow">Bookshelf</p>
          <h1 id="bookshelf-title">Choose a book</h1>
        </div>
        <button className="ghost-button" type="button" onClick={onBack}>
          Back to Today
        </button>
      </header>

      {notice ? (
        <p className="bookshelf-notice" role="status" data-testid="bookshelf-notice">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      <ul className="bookshelf-list">
        {books.map((book) => (
          <li key={book.id}>
            <button
              type="button"
              className="bookshelf-item"
              data-testid="bookshelf-item"
              data-book-id={book.id}
              aria-current={book.isCurrent ? 'true' : undefined}
              disabled={isSwitching || book.totalWords === 0}
              onClick={() => handleBookClick(book)}
            >
              <span
                className={
                  coverClassFor(book.id)
                    ? `bookshelf-cover ${coverClassFor(book.id)}`
                    : 'bookshelf-cover'
                }
                aria-hidden="true"
              >
                <span className="bookshelf-cover-spine" />
                <span className="bookshelf-cover-title" title={book.title}>
                  {book.title}
                </span>
              </span>
              <span className="bookshelf-meta">
                <span className="bookshelf-item-title" title={book.title}>
                  {book.title}
                  {book.isCurrent ? <span className="current-book-badge">Current</span> : null}
                </span>
                <span className="bookshelf-item-stats">
                  {book.totalWords} words · {book.learnedWords ?? 0} learned · {book.masteredWords ?? 0} mastered
                </span>
                {book.totalWords === 0 ? (
                  <span className="bookshelf-item-hint">Data not ready — coming soon</span>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {books.length < 2 ? (
        <p className="bookshelf-empty-note" data-testid="bookshelf-empty-note">
          More books will be added through import
        </p>
      ) : null}

      {confirmTarget ? (
        <div className="bookshelf-confirm-backdrop" role="presentation">
          <div
            className="bookshelf-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bookshelf-confirm-title"
            data-testid="bookshelf-confirm"
          >
            <h2 id="bookshelf-confirm-title">Switch books</h2>
            <p>You will study “{confirmTarget.title}”. Your progress in the current book is kept.</p>
            <div className="bookshelf-confirm-actions">
              <button
                className="ghost-button"
                type="button"
                onClick={() => setConfirmTarget(null)}
              >
                Cancel
              </button>
              <button
                className="primary-action"
                type="button"
                onClick={() => void handleConfirmSwitch()}
              >
                Switch
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
