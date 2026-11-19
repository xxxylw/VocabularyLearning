import { afterEach, describe, expect, it, vi } from 'vitest';
import { getBookProgress, lookupOxfordWord, reviewCard, reviewRepeatPoolCard, startTodaySession } from './api';

describe('api', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('starts today session with the default new-word target', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ totalCards: 0, cards: [] }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await startTodaySession();

    // P0 2026-09-08: extraNewWords is always present in the request
    // body so the backend's merge path can decide whether to append a
    // 加练 group. The default 0 keeps the existing single-pass flow.
    expect(fetchMock).toHaveBeenCalledWith('/api/study/today/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dailyNewWordTarget: 20, extraNewWords: 0 })
    });
  });

  it('starts today session with a custom new-word target', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ totalCards: 0, cards: [] }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await startTodaySession(12);

    expect(fetchMock).toHaveBeenCalledWith('/api/study/today/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dailyNewWordTarget: 12, extraNewWords: 0 })
    });
  });

  it('loads book progress', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ totalWords: 0, nextSequenceIndex: null }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(getBookProgress()).resolves.toEqual({ totalWords: 0, nextSequenceIndex: null });

    expect(fetchMock).toHaveBeenCalledWith('/api/book-words/progress');
  });

  it('looks up a selected word through the local Oxford proxy', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            word: 'atmosphere',
            sourceUrl: 'https://www.oxfordlearnersdictionaries.com/definition/english/atmosphere?q=atmosphere',
            senses: [
              {
                partOfSpeech: 'noun',
                definition: 'the mixture of gases that surrounds the earth',
                example: 'Wind power does not release carbon dioxide into the atmosphere.'
              }
            ]
          })
        )
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(lookupOxfordWord('atmosphere')).resolves.toMatchObject({
      word: 'atmosphere',
      senses: [
        {
          partOfSpeech: 'noun',
          definition: 'the mixture of gases that surrounds the earth'
        }
      ]
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/lookup/oxford?word=atmosphere');
  });

  it('reviews a card with ISO reviewedAt and local YYYY-MM-DD reviewedDate', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 9, 30, 0));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ nextDueDate: '2026-07-02' }))
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

  it('throws a useful error when an API error response is not JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: () => Promise.resolve('upstream unavailable')
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(startTodaySession()).rejects.toThrow(
      'POST /api/study/today/start failed with 500 Internal Server Error: upstream unavailable'
    );
  });

  it('rejects review conflicts so unsaved cards do not advance', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({ message: 'Review already exists for this card today' }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(reviewCard('card-1', 'known')).rejects.toThrow(
      'POST /api/cards/card-1/reviews failed with 409 Conflict: Review already exists for this card today'
    );
  });

  // 当日重复池（task 7684082688076025051）：重复卡评分走池端点，只更新
  // 池状态、不写 reviews。
  it('submits repeat-pool reviews to the pool endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () =>
        Promise.resolve(JSON.stringify({ cardId: 'card-1', status: 'cleared', repeatCount: 1 }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(reviewRepeatPoolCard('card-1', 'known')).resolves.toEqual({
      cardId: 'card-1',
      status: 'cleared',
      repeatCount: 1
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/study/today/repeat-pool/reviews', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cardId: 'card-1', rating: 'known' })
    });
  });

  it('treats a 404 from the pool endpoint as an idempotent skip for unpooled sibling cards', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: () => Promise.resolve(JSON.stringify({ detail: 'Repeat pool entry not found' }))
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(reviewRepeatPoolCard('card-9', 'known')).resolves.toBeNull();
  });

  it('rethrows non-404 pool endpoint failures', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: () => Promise.resolve('upstream unavailable')
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(reviewRepeatPoolCard('card-1', 'known')).rejects.toThrow(
      'POST /api/study/today/repeat-pool/reviews failed with 500 Internal Server Error: upstream unavailable'
    );
  });

});
