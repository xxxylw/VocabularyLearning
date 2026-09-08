import { fireEvent, render, screen } from '@testing-library/react';
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

  it('renders 「再来一组 / 练习拼写」and hides Start when dayCompleted (P0 acceptance #1)', async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    const onAnotherGroup = vi.fn();
    const onPracticeSpelling = vi.fn();

    render(
      <TodayView
        onStart={onStart}
        onAnotherGroup={onAnotherGroup}
        onPracticeSpelling={onPracticeSpelling}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        dayCompleted
        canPracticeSpelling
      />
    );

    // The Start button is gone — replaced by the completion-set buttons.
    expect(screen.queryByRole('button', { name: /start today cards/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('today-day-completed')).toHaveTextContent('今日卡片已背完');
    expect(screen.getByTestId('another-group')).toBeInTheDocument();
    expect(screen.getByTestId('practice-spelling-completed')).toBeInTheDocument();

    await user.click(screen.getByTestId('another-group'));
    expect(onAnotherGroup).toHaveBeenCalledTimes(1);
    expect(onStart).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('practice-spelling-completed'));
    expect(onPracticeSpelling).toHaveBeenCalledTimes(1);
  });

  it('hides the practice-spelling button when dayCompleted but canPracticeSpelling is false', () => {
    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        dayCompleted
        canPracticeSpelling={false}
      />
    );

    expect(screen.getByTestId('another-group')).toBeInTheDocument();
    expect(screen.queryByTestId('practice-spelling-completed')).not.toBeInTheDocument();
  });

  it('renders the Start today cards button when dayCompleted is false (existing behavior)', () => {
    const onStart = vi.fn();
    render(
      <TodayView
        onStart={onStart}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        dayCompleted={false}
      />
    );

    expect(screen.getByRole('button', { name: /start today cards/i })).toBeInTheDocument();
    expect(screen.queryByTestId('another-group')).not.toBeInTheDocument();
  });

  it('renders the finish estimate on the book cover card using the median 14-day new-word speed', () => {
    const checkIns = [
      { date: '2026-08-26', completedCards: 10, newCards: 10, reviewCards: 0, completedAt: '' },
      { date: '2026-09-06', completedCards: 20, newCards: 20, reviewCards: 0, completedAt: '' },
      { date: '2026-09-07', completedCards: 20, newCards: 20, reviewCards: 0, completedAt: '' },
      { date: '2026-09-08', completedCards: 20, newCards: 20, reviewCards: 0, completedAt: '' }
    ];

    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        bookTitle="词书"
        bookTotalWords={220}
        bookLearnedWords={20}
        checkIns={checkIns}
        onOpenBookShelf={vi.fn()}
      />
    );

    // 中位速度 20 词/天、剩余 200 词 → 10 天；10 ≤ 30，附完成日期。
    const estimate = screen.getByTestId('book-cover-estimate');
    expect(estimate.textContent).toMatch(/按每天 20 词的节奏，预计还需 10 天背完/);
  });

  it('shows the new-words-done copy on the cover card when the book is finished', () => {
    render(
      <TodayView
        onStart={vi.fn()}
        isLoading={false}
        newWordTarget={20}
        onNewWordTargetChange={vi.fn()}
        bookTitle="词书"
        bookTotalWords={100}
        bookLearnedWords={100}
        onOpenBookShelf={vi.fn()}
      />
    );

    expect(screen.getByTestId('book-cover-estimate')).toHaveTextContent('新词已学完');
  });

  it('recomputes the estimate when the new-word target changes', () => {
    function Harness() {
      const [target, setTarget] = useState(20);
      return (
        <TodayView
          onStart={vi.fn()}
          isLoading={false}
          newWordTarget={target}
          onNewWordTargetChange={setTarget}
          bookTitle="词书"
          bookTotalWords={200}
          bookLearnedWords={0}
          onOpenBookShelf={vi.fn()}
        />
      );
    }

    render(<Harness />);

    // 无打卡样本（< 3 天）→ 速度回退目标值，目标 20 → 10 天。
    expect(screen.getByTestId('book-cover-estimate').textContent).toMatch(/预计还需 10 天/);

    const input = screen.getByRole('spinbutton', { name: /new word target/i });
    input.setAttribute('value', '40');
    // userEvent.type to drive React onChange so the parent state updates.
    // We use fireEvent for simplicity on a controlled input where the parent
    // owns the value, and rely on the rendered estimate updating on re-render.
    fireEvent.change(input, { target: { value: '40' } });

    // 速度回退为新目标 40 → 200/40 = 5 天。
    expect(screen.getByTestId('book-cover-estimate').textContent).toMatch(/预计还需 5 天/);
  });
});
