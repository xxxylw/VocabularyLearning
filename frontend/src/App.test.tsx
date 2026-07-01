import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows an import-needed empty state when today starts with no book words', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 0, cards: [] }))
      })
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalWords: 0, nextSequenceIndex: null }))
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(await screen.findByText('No book words imported yet.')).toBeInTheDocument();
    expect(screen.queryByText('No cards are waiting today.')).not.toBeInTheDocument();
  });
});
