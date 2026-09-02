import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TodayView } from './TodayView';

describe('TodayView', () => {
  afterEach(() => {
    vi.useRealTimers();
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
});
