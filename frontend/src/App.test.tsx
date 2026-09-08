import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    // P1 2026-09-08 打卡服务端化：handleSessionComplete 会写 localStorage，
    // 不清理会串测试（下一个用例 mount 会触发 merge 上报、多消费一条
    // fetch 队列）。
    window.localStorage.clear();
  });

  it('shows an import-needed empty state when today starts with no book words', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      // P0 2026-09-08: App now fetches /api/study/today/summary on mount
      // so the Today page can render the correct button set on first
      // paint (no flicker, no cross-device state loss).
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
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
    expect(await screen.findByText('单词书：雅思词汇真经')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(await screen.findByText('No book words imported yet.')).toBeInTheDocument();
    expect(screen.queryByText('No cards are waiting today.')).not.toBeInTheDocument();
  });

  it('starts spelling practice from the completed study session', async () => {
    const user = userEvent.setup();
    const completedCard = studyCard();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [completedCard] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      })
      // handleSessionComplete fires a follow-up summary fetch
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]));
      // P1 2026-09-08 打卡服务端化：会话完成后拉一次 GET /api/check-ins。
    fetchMock.mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /practice spelling/i }));

    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
    // F-01: the chinese note contains the answer, so the prompt must use
    // the safe definition sentence instead.
    expect(screen.queryByText('El Nino phenomenon')).not.toBeInTheDocument();
    expect(screen.getByText('a weather pattern that warms the eastern Pacific Ocean')).toBeInTheDocument();
  });

  it('offers spelling practice when today has no more cards after a completed session', async () => {
    // P0 2026-09-08: the old flicker path (click Start today cards on
    // a finished day → empty-state section) is gone. After completion
    // the page swaps to 「再来一组 / 练习拼写」, so the test now
    // exercises that flow: complete one card → land back on Today →
    // click 练习拼写 → spelling view.
    const user = userEvent.setup();
    const completedCard = studyCard();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [completedCard] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      })
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]));
      // P1 2026-09-08 打卡服务端化：会话完成后拉一次 GET /api/check-ins。
    fetchMock.mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /back home/i }));

    // Old flicker path is gone — no Start today cards in completed state.
    expect(screen.queryByRole('button', { name: /start today cards/i })).not.toBeInTheDocument();
    // New completion-set buttons render; 练习拼写 leads into spelling view.
    expect(screen.getByTestId('another-group')).toBeInTheDocument();
    expect(screen.getByTestId('practice-spelling-completed')).toBeInTheDocument();
    await user.click(screen.getByTestId('practice-spelling-completed'));

    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
  });

  it('shows a home spelling button after a completed study session', async () => {
    // P0 2026-09-08: post-completion, the home spelling entry is the
    // 「练习拼写」completion-set button (not the old "Practice spelling"
    // English secondary that lived next to Start today cards).
    const user = userEvent.setup();
    const completedCard = studyCard();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [completedCard] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      })
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]));
      // P1 2026-09-08 打卡服务端化：会话完成后拉一次 GET /api/check-ins。
    fetchMock.mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /back home/i }));

    // Click 练习拼写 in the completion set.
    await user.click(screen.getByTestId('practice-spelling-completed'));

    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
  });

  it('resumes today progress from the day queue after re-entering (PRD ch.8)', async () => {
    const user = userEvent.setup();
    const resumedCard = { ...studyCard(), queuePosition: 11 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      .mockResolvedValueOnce({
        ok: true,
        text: () =>
          Promise.resolve(
            JSON.stringify({ totalCards: 40, reviewedCards: 10, cards: [resumedCard] })
          )
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(await screen.findByText('11 / 40')).toBeInTheDocument();
    expect(screen.getByText('10 / 40 completed')).toBeInTheDocument();
    const progress = screen.getByRole('progressbar', { name: /Today completed words/i });
    expect(progress).toHaveAttribute('aria-valuenow', '10');
    expect(progress).toHaveAttribute('aria-valuemax', '40');
  });
  it('switches the current book from the bookshelf and returns to Today (PRD ch.9)', async () => {
    const user = userEvent.setup();
    const switchedBook = currentBookResponse({ id: 'book-b', title: '托福核心词汇', totalWords: 4100, learnedWords: 5 });
    const fetchMock = vi
      .fn()
      // initial current book (default) — cover card aggregates
      .mockResolvedValueOnce(currentBookResponse({ learnedWords: 120, masteredWords: 30 }))
      // initial summary on mount
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      // GET /api/books when the cover card opens the bookshelf
      .mockResolvedValueOnce({
        ok: true,
        text: () =>
          Promise.resolve(
            JSON.stringify({
              books: [
                {
                  id: 'book-default',
                  title: '雅思词汇真经',
                  totalWords: 3383,
                  learnedWords: 120,
                  masteredWords: 30,
                  isCurrent: true
                },
                {
                  id: 'book-b',
                  title: '托福核心词汇',
                  totalWords: 4100,
                  learnedWords: 5,
                  masteredWords: 0,
                  isCurrent: false
                }
              ]
            })
          )
      })
      // PUT /api/books/current — the switch itself
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify(switchedBook))
      })
      // GET /api/books/current — Today refresh after the switch
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify(switchedBook))
      })
      // refreshTodaySummary fires after the switch
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // Cover card shows the current book's aggregates from the initial
    // GET /api/books/current response.
    expect(await screen.findByText('已学 120 / 3383')).toBeInTheDocument();
    await user.click(screen.getByTestId('book-cover-card'));

    // Bookshelf: list + current badge + confirm dialog before switching.
    expect(await screen.findByRole('heading', { name: '选择单词书' })).toBeInTheDocument();
    expect(screen.getByText('当前')).toBeInTheDocument();
    await user.click(screen.getAllByTestId('bookshelf-item')[1]);
    const dialog = await screen.findByTestId('bookshelf-confirm');
    expect(dialog).toHaveTextContent('切换后将学习《托福核心词汇》，当前书的学习进度会保留。');

    await user.click(screen.getByRole('button', { name: /确认切换/ }));

    // The switch PUT fires with the targeted bookId.
    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        (call) => typeof call[1] === 'object' && call[1]?.method === 'PUT'
      );
      expect(putCall?.[0]).toBe('/api/books/current');
      expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({ bookId: 'book-b' });
    });
    // Back at Today: the bookshelf is gone, the desk panel and check-in
    // grid are present. The cover card update is verified separately by
    // TodayView.test.tsx, which keeps this integration test focused on
    // the click → PUT → return-to-Today flow.
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: '选择单词书' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /start today cards/i })).toBeInTheDocument();
  });

  it('cross-device: restores the day-completed button set when today summary reports dayCompleted (P0 acceptance #2)', async () => {
    // Acceptance #2: phone completes → computer refreshes. The page
    // must render 「再来一组 + 练习拼写」right after the mount-time
    // summary fetch — no flicker, no start button.
    const completedCard = studyCard();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      // mount-time summary fetch says day is complete (cross-device state)
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]));
      // P1 2026-09-08 打卡服务端化：会话完成后拉一次 GET /api/check-ins。
    fetchMock.mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // Wait for the completion-set banner — once it appears, the Start
    // button must already be gone. (findBy* awaits the React commit,
    // so the subsequent queryBy* assertion observes the same render.)
    expect(await screen.findByTestId('today-day-completed')).toHaveTextContent('今日卡片已背完');
    expect(screen.queryByRole('button', { name: /start today cards/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('another-group')).toBeInTheDocument();
    expect(screen.getByTestId('practice-spelling-completed')).toBeInTheDocument();
  });

  it('swaps Start today cards for 「再来一组 / 练习拼写」after the day queue completes (P0 acceptance #1)', async () => {
    const user = userEvent.setup();
    const completedCard = studyCard();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // P1 2026-09-08 打卡服务端化：mount 时还会拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      // start the day's first session
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [completedCard] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      })
      // session-complete summary refresh flips dayCompleted to true
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]));
      // P1 2026-09-08 打卡服务端化：会话完成后拉一次 GET /api/check-ins。
    fetchMock.mockResolvedValueOnce(checkInsResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    // Phase 1: not yet completed, Start button shows.
    expect(await screen.findByRole('button', { name: /start today cards/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    // Walk through the single card → session-complete screen.
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    // Back to Today via the completion screen's "Back home" button.
    await user.click(await screen.findByRole('button', { name: /back home/i }));
    // Once the completion-set banner renders, the Start button must
    // be gone and the new pair must be present.
    expect(await screen.findByTestId('today-day-completed')).toHaveTextContent('今日卡片已背完');
    expect(screen.queryByRole('button', { name: /start today cards/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('another-group')).toBeInTheDocument();
    expect(screen.getByTestId('practice-spelling-completed')).toBeInTheDocument();

    // Tapping 练习拼写 should land in spelling view, pulling cards
    // from the server summary.
    await user.click(screen.getByTestId('practice-spelling-completed'));
    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
  });

  it('「再来一组」sends extraNewWords in the today/start request body', async () => {
    const user = userEvent.setup();
    const completedCard = studyCard();
    const extraCard = { ...studyCard(), cardId: 'card-2', word: 'La Nina' };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard]))
      // P1 2026-09-08 打卡服务端化：mount / 会话完成后拉一次 GET /api/check-ins。
      .mockResolvedValueOnce(checkInsResponse([]))
      // POST /api/study/today/start with extraNewWords after 再来一组 click
      .mockResolvedValueOnce({
        ok: true,
        text: () =>
          Promise.resolve(
            JSON.stringify({ totalCards: 2, reviewedCards: 0, cards: [extraCard] })
          )
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      // refreshTodaySummary fires after the start
      .mockResolvedValueOnce(completedDaySummaryResponse([completedCard, extraCard]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(await screen.findByTestId('another-group'));

    // The POST body for /api/study/today/start must include
    // extraNewWords equal to the newWordTarget (default 20 here).
    await waitFor(() => {
      const startCall = fetchMock.mock.calls.find(
        (call) =>
          typeof call[0] === 'string' &&
          call[0].startsWith('/api/study/today/start') &&
          typeof call[1] === 'object' &&
          (call[1] as RequestInit | undefined)?.method === 'POST'
      );
      expect(startCall).toBeDefined();
      expect(JSON.parse(String((startCall?.[1] as RequestInit).body))).toEqual({
        dailyNewWordTarget: 20,
        extraNewWords: 20
      });
    });
  });
});

function emptyDaySummaryResponse() {
  return {
    ok: true,
    text: () =>
      Promise.resolve(
        JSON.stringify({
          studyDate: '2026-09-08',
          totalCards: 0,
          reviewedCards: 0,
          dayCompleted: false,
          completedCards: []
        })
      )
  };
}

// P1 2026-09-08 打卡服务端化：GET /api/check-ins 的响应桩。
function checkInsResponse(records: unknown[]) {
  return {
    ok: true,
    text: () => Promise.resolve(JSON.stringify({ checkIns: records }))
  };
}

function completedDaySummaryResponse(cards: unknown[]) {
  return {
    ok: true,
    text: () =>
      Promise.resolve(
        JSON.stringify({
          studyDate: '2026-09-08',
          totalCards: cards.length,
          reviewedCards: cards.length,
          dayCompleted: true,
          completedCards: cards
        })
      )
  };
}

function currentBookResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    text: () => Promise.resolve(JSON.stringify({
      id: 'book-default',
      title: '雅思词汇真经',
      description: 'IELTS vocabulary book imported from the default CSV.',
      source: 'book_words.csv',
      createdAt: '2026-07-01T00:00:00Z',
      updatedAt: '2026-07-01T00:00:00Z',
      totalWords: 3383,
      ...overrides
    }))
  };
}

function studyCard() {
  return {
    cardId: 'card-1',
    cardIds: ['card-1'],
    word: 'El Nino',
    partOfSpeech: 'noun',
    senseLabel: 'weather pattern',
    definition: 'a weather pattern that warms the eastern Pacific Ocean',
    definitionSource: 'oxford_api',
    examples: [],
    chineseNote: 'El Nino phenomenon',
    senses: [
      {
        cardId: 'card-1',
        partOfSpeech: 'noun',
        senseLabel: 'weather pattern',
        definition: 'a weather pattern that warms the eastern Pacific Ocean',
        definitionSource: 'oxford_api',
        examples: [],
        chineseNote: 'El Nino phenomenon'
      }
    ],
    status: 'new',
    stage: 0,
    dueAt: '2026-07-04',
    queueType: 'new',
    degraded: false
  };
}

function pronunciationUnavailableResponse() {
  return {
    ok: true,
    text: () => Promise.resolve(JSON.stringify({
      word: 'el nino',
      ipa: null,
      audioUrl: null,
      sourceUrl: 'https://en.wiktionary.org/wiki/El_Nino#English',
      audioSourceUrl: null,
      attribution: null,
      license: null,
      licenseUrl: null,
      status: 'unavailable'
    }))
  };
}
