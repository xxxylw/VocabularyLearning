import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportFullBook, reviewCard, startTodaySession } from './api';

describe('api', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('starts today session with the default new-word target', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ totalCards: 0, cards: [] })
    });
    vi.stubGlobal('fetch', fetchMock);

    await startTodaySession();

    expect(fetchMock).toHaveBeenCalledWith('/api/study/today/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dailyNewWordTarget: 20 })
    });
  });

  it('reviews a card with ISO reviewedAt and local YYYY-MM-DD reviewedDate', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 9, 30, 0));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ nextDueDate: '2026-07-02' })
    });
    vi.stubGlobal('fetch', fetchMock);

    await reviewCard('card-1', 'known');

    expect(fetchMock).toHaveBeenCalledWith('/api/cards/card-1/reviews', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        rating: 'known',
        reviewedAt: new Date(2026, 6, 1, 9, 30, 0).toISOString(),
        reviewedDate: '2026-07-01'
      })
    });
  });

  it('requests the full-book Anki export with the MVP defaults', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ downloadUrl: '/download.apkg', cardCount: 42 })
    });
    vi.stubGlobal('fetch', fetchMock);

    await exportFullBook();

    expect(fetchMock).toHaveBeenCalledWith('/api/export/anki/full-book', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deckName: 'Vocabulary Learning Full Book',
        includeChineseNote: true
      })
    });
  });
});
