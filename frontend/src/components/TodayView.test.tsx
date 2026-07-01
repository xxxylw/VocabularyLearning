import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { TodayView } from './TodayView';

describe('TodayView', () => {
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
});
