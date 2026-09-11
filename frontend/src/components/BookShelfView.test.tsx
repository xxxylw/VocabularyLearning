import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { BookListItem } from '../api';
import { BookShelfView } from './BookShelfView';

function makeBook(overrides: Partial<BookListItem> = {}): BookListItem {
  return {
    id: 'book-default',
    title: '雅思词汇真经',
    description: null,
    source: null,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-01T00:00:00Z',
    totalWords: 3383,
    learnedWords: 120,
    masteredWords: 30,
    isCurrent: true,
    ...overrides
  };
}

describe('BookShelfView', () => {
  it('renders the book list with aggregates and marks the current book', () => {
    render(
      <BookShelfView
        books={[makeBook(), makeBook({ id: 'book-b', title: '托福核心词汇', isCurrent: false })]}
        onBack={vi.fn()}
        onSwitch={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { name: 'Choose a book' })).toBeInTheDocument();
    const items = screen.getAllByTestId('bookshelf-item');
    expect(items).toHaveLength(2);
    const current = items.find((el) => el.getAttribute('aria-current') === 'true');
    expect(current).toBeDefined();
    expect(current).toHaveAttribute('data-book-id', 'book-default');
    expect(within(current!).getByText('Current')).toBeInTheDocument();
    expect(within(current!).getByText(/3383 words · 120 learned · 30 mastered/)).toBeInTheDocument();
    expect(screen.queryByTestId('bookshelf-empty-note')).not.toBeInTheDocument();
  });

  it('shows the v1 empty note when only the current book exists', () => {
    render(<BookShelfView books={[makeBook()]} onBack={vi.fn()} onSwitch={vi.fn()} />);

    expect(screen.getByTestId('bookshelf-empty-note')).toHaveTextContent(
      'More books will be added through import'
    );
  });

  it('gives the red book a red cover variant while other books keep the default', () => {
    render(
      <BookShelfView
        books={[
          makeBook(),
          makeBook({ id: 'kaoyan-hongbaoshu-2027', title: '考研英语红宝书', isCurrent: false })
        ]}
        onBack={vi.fn()}
        onSwitch={vi.fn()}
      />
    );

    const items = screen.getAllByTestId('bookshelf-item');
    const defaultCover = items[0].querySelector('.bookshelf-cover');
    const redCover = items[1].querySelector('.bookshelf-cover');
    expect(defaultCover).not.toBeNull();
    expect(defaultCover).toHaveClass('bookshelf-cover');
    expect(defaultCover).not.toHaveClass('bookshelf-cover--red');
    expect(redCover).not.toBeNull();
    expect(redCover).toHaveClass('bookshelf-cover', 'bookshelf-cover--red');
  });

  it('opens a confirm dialog before switching and calls onSwitch only after confirming', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const target = makeBook({ id: 'book-b', title: '托福核心词汇', isCurrent: false });

    render(
      <BookShelfView books={[makeBook(), target]} onBack={vi.fn()} onSwitch={onSwitch} />
    );

    await user.click(screen.getAllByTestId('bookshelf-item')[1]);

    const dialog = await screen.findByTestId('bookshelf-confirm');
    expect(dialog).toHaveTextContent('You will study “托福核心词汇”. Your progress in the current book is kept.');
    expect(onSwitch).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^Switch$/ }));
    expect(onSwitch).toHaveBeenCalledWith('book-b');
  });

  it('cancel closes the dialog without switching', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const target = makeBook({ id: 'book-b', title: '托福核心词汇', isCurrent: false });

    render(
      <BookShelfView books={[makeBook(), target]} onBack={vi.fn()} onSwitch={onSwitch} />
    );

    await user.click(screen.getAllByTestId('bookshelf-item')[1]);
    await user.click(screen.getByRole('button', { name: /^Cancel$/ }));

    expect(screen.queryByTestId('bookshelf-confirm')).not.toBeInTheDocument();
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it('does not open the confirm dialog for the already-current book', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();

    render(<BookShelfView books={[makeBook()]} onBack={vi.fn()} onSwitch={onSwitch} />);

    await user.click(screen.getByTestId('bookshelf-item'));
    expect(screen.queryByTestId('bookshelf-confirm')).not.toBeInTheDocument();
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it('renders a zero-word book as disabled and unselectable', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const empty = makeBook({ id: 'book-empty', title: '空书', totalWords: 0, isCurrent: false });

    render(<BookShelfView books={[makeBook(), empty]} onBack={vi.fn()} onSwitch={onSwitch} />);

    const item = screen.getAllByTestId('bookshelf-item')[1];
    expect(item).toBeDisabled();
    await user.click(item);
    expect(screen.queryByTestId('bookshelf-confirm')).not.toBeInTheDocument();
  });

  it('calls onBack from the return-to-Today button', async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();

    render(<BookShelfView books={[makeBook()]} onBack={onBack} onSwitch={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /Back to Today/ }));
    expect(onBack).toHaveBeenCalled();
  });

  it('shows the fallback notice when the pointer referenced a missing book', () => {
    render(
      <BookShelfView
        books={[makeBook()]}
        onBack={vi.fn()}
        onSwitch={vi.fn()}
        notice="Current book not found. Switched to the default book “雅思词汇真经”."
      />
    );

    expect(screen.getByTestId('bookshelf-notice')).toHaveTextContent('Switched to the default book');
  });

  it('renders no finish-estimate line on the shelf (estimate only lives on Today cover)', () => {
    // 2026-09-11 需求变更：选书页不再标注「还有多久背完」，主页 Today 封面卡保留。
    const bookA = makeBook({ id: 'book-a', title: '雅思词汇真经', totalWords: 100, learnedWords: 60, masteredWords: 0 });
    const bookB = makeBook({ id: 'book-b', title: '考研红宝书', totalWords: 200, learnedWords: 0, masteredWords: 0 });
    const finished = makeBook({ id: 'book-c', title: '已学完的书', totalWords: 100, learnedWords: 100, masteredWords: 0 });

    render(<BookShelfView books={[bookA, bookB, finished]} onBack={vi.fn()} onSwitch={vi.fn()} />);

    expect(screen.queryByTestId('bookshelf-estimate-book-a')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bookshelf-estimate-book-b')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bookshelf-estimate-book-c')).not.toBeInTheDocument();
    expect(screen.queryByText(/预计还需/)).not.toBeInTheDocument();
    expect(screen.queryByText(/背完/)).not.toBeInTheDocument();
    expect(screen.queryByText(/新词已学完/)).not.toBeInTheDocument();
  });
});
