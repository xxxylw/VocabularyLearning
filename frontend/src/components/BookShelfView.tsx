import { useState } from 'react';
import type { BookListItem } from '../api';

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
          <h1 id="bookshelf-title">选择单词书</h1>
        </div>
        <button className="ghost-button" type="button" onClick={onBack}>
          返回 Today
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
              <span className="bookshelf-cover" aria-hidden="true">
                <span className="bookshelf-cover-spine" />
                <span className="bookshelf-cover-title" title={book.title}>
                  {book.title}
                </span>
              </span>
              <span className="bookshelf-meta">
                <span className="bookshelf-item-title" title={book.title}>
                  {book.title}
                  {book.isCurrent ? <span className="current-book-badge">当前</span> : null}
                </span>
                <span className="bookshelf-item-stats">
                  {book.totalWords} 词 · 已学 {book.learnedWords ?? 0} · 已掌握 {book.masteredWords ?? 0}
                </span>
                {book.totalWords === 0 ? (
                  <span className="bookshelf-item-hint">数据未就绪，暂不可选</span>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {books.length < 2 ? (
        <p className="bookshelf-empty-note" data-testid="bookshelf-empty-note">
          更多单词书将通过导入功能加入（规划中）
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
            <h2 id="bookshelf-confirm-title">切换单词书</h2>
            <p>切换后将学习《{confirmTarget.title}》，当前书的学习进度会保留。</p>
            <div className="bookshelf-confirm-actions">
              <button
                className="ghost-button"
                type="button"
                onClick={() => setConfirmTarget(null)}
              >
                取消
              </button>
              <button
                className="primary-action"
                type="button"
                onClick={() => void handleConfirmSwitch()}
              >
                确认切换
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
