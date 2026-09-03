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

    expect(screen.getByRole('heading', { name: '选择单词书' })).toBeInTheDocument();
    const items = screen.getAllByTestId('bookshelf-item');
    expect(items).toHaveLength(2);
    const current = items.find((el) => el.getAttribute('aria-current') === 'true');
    expect(current).toBeDefined();
    expect(current).toHaveAttribute('data-book-id', 'book-default');
    expect(within(current!).getByText('当前')).toBeInTheDocument();
    expect(within(current!).getByText(/3383 词 · 已学 120 · 已掌握 30/)).toBeInTheDocument();
    expect(screen.queryByTestId('bookshelf-empty-note')).not.toBeInTheDocument();
  });

  it('shows the v1 empty note when only the current book exists', () => {
    render(<BookShelfView books={[makeBook()]} onBack={vi.fn()} onSwitch={vi.fn()} />);

    expect(screen.getByTestId('bookshelf-empty-note')).toHaveTextContent(
      '更多单词书将通过导入功能加入（规划中）'
    );
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
    expect(dialog).toHaveTextContent('切换后将学习《托福核心词汇》，当前书的学习进度会保留。');
    expect(onSwitch).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /确认切换/ }));
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
    await user.click(screen.getByRole('button', { name: /取消/ }));

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

    await user.click(screen.getByRole('button', { name: /返回 Today/ }));
    expect(onBack).toHaveBeenCalled();
  });

  it('shows the fallback notice when the pointer referenced a missing book', () => {
    render(
      <BookShelfView
        books={[makeBook()]}
        onBack={vi.fn()}
        onSwitch={vi.fn()}
        notice="当前书不存在，已回退默认书「雅思词汇真经」"
      />
    );

    expect(screen.getByTestId('bookshelf-notice')).toHaveTextContent('已回退默认书');
  });
});
