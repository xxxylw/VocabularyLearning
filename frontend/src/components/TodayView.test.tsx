import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TodayView } from './TodayView';

describe('TodayView', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows "@email " as the prefix of the today-note sentence when userEmail is provided', () => {
    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        userEmail="qirui.huang@flexiv.com"
      />
    );

    const email = screen.getByTestId('today-user-email');
    expect(email).toHaveTextContent('@qirui.huang@flexiv.com');
    // Full address stays available via the title attribute.
    expect(email).toHaveAttribute('title', 'qirui.huang@flexiv.com');
    // The @email prefix and the note sentence share one paragraph, email first.
    const note = email.closest('p');
    expect(note).toHaveTextContent(
      '@qirui.huang@flexiv.com A quiet desk, a short queue, and a focused pass through the words waiting for you.'
    );
  });

  it('does not render the email element when userEmail is missing', () => {
    render(<TodayView onStart={vi.fn()} isLoading={false} newWordTarget={20} onNewWordTargetChange={vi.fn()} />);

    expect(screen.queryByTestId('today-user-email')).not.toBeInTheDocument();
    // The note sentence still renders on its own.
    expect(
      screen.getByText('A quiet desk, a short queue, and a focused pass through the words waiting for you.')
    ).toBeInTheDocument();
  });

  it('no longer renders the "go see the world" Chinese motto', () => {
    render(<TodayView onStart={vi.fn()} isLoading={false} newWordTarget={20} onNewWordTargetChange={vi.fn()} />);

    expect(screen.queryByText('背下的每个词，都是去看世界的路')).not.toBeInTheDocument();
  });

  it('renders Start today cards and calls onStart when clicked', async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    render(<TodayView onStart={onStart} isLoading={false} newWordTarget={20} onNewWordTargetChange={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(onStart).toHaveBeenCalledWith(20);
  });

  it('lets the user change the new word target before starting', async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    function Harness() {
      const [newWordTarget, setNewWordTarget] = useState(20);

      return (
        <TodayView
          onStart={onStart}
          isLoading={false}
          newWordTarget={newWordTarget}
          onNewWordTargetChange={setNewWordTarget}
        />
      );
    }

    render(<Harness />);

    const input = screen.getByRole('spinbutton', { name: /new word target/i });
    await user.clear(input);
    await user.type(input, '12');
    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(onStart).toHaveBeenCalledWith(12);
  });

  it('shows the daily check-in grid on the home screen', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 4, 9, 0, 0));

    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        checkIns={[
          {
            date: '2026-07-04',
            completedCards: 24,
            newCards: 20,
            reviewCards: 4,
            completedAt: '2026-07-04T08:00:00.000Z'
          }
        ]}
      />
    );

    expect(screen.getByRole('heading', { name: /study rhythm/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/2026-07-04: 24 cards completed/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/1 day streak/i)).toBeInTheDocument();
  });

  it('renders the current book cover card with title, words and progress (PRD ch.9)', () => {
    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        bookTitle="雅思词汇真经"
        bookTotalWords={3383}
        bookLearnedWords={120}
        onOpenBookShelf={vi.fn()}
      />
    );

    const cover = screen.getByTestId('book-cover-card');
    expect(cover).toHaveTextContent('雅思词汇真经');
    expect(cover).toHaveTextContent('3383 词');
    expect(cover).toHaveTextContent('已学 120 / 3383');
    // Full title stays available via the title attribute when truncated.
    expect(cover.querySelector('.book-cover-title')).toHaveAttribute('title', '雅思词汇真经');
  });

  it('keeps the cover card accessible when aggregates are not loaded yet', () => {
    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        bookTitle="雅思词汇真经"
        onOpenBookShelf={vi.fn()}
      />
    );

    const cover = screen.getByTestId('book-cover-card');
    expect(cover).toHaveTextContent('雅思词汇真经');
    expect(cover.textContent).not.toContain('已学');
  });

  it('opens the bookshelf when the cover card is clicked', async () => {
    const user = userEvent.setup();
    const onOpenBookShelf = vi.fn();

    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        bookTitle="雅思词汇真经"
        bookTotalWords={3383}
        bookLearnedWords={120}
        onOpenBookShelf={onOpenBookShelf}
      />
    );

    await user.click(screen.getByTestId('book-cover-card'));
    expect(onOpenBookShelf).toHaveBeenCalled();
  });
});
