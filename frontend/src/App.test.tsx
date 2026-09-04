import { render, screen, waitFor } from '@testing-library/react';
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
      .mockResolvedValueOnce(currentBookResponse())
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
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [studyCard()] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      });
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
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [studyCard()] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      })
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 0, cards: [] }))
      })
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalWords: 10, nextSequenceIndex: 2 }))
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /back home/i }));
    await user.click(screen.getByRole('button', { name: /start today cards/i }));

    expect(await screen.findByText(/today's card queue is clear/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /practice spelling now/i }));

    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
  });

  it('shows a home spelling button after a completed study session', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ totalCards: 1, cards: [studyCard()] }))
      })
      .mockResolvedValueOnce(pronunciationUnavailableResponse())
      .mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ cardId: 'card-1' }))
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('button', { name: /start today cards/i }));
    await user.click(await screen.findByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /back home/i }));
    await user.click(screen.getByRole('button', { name: /^practice spelling$/i }));

    expect(await screen.findByRole('main', { name: /spelling practice/i })).toBeInTheDocument();
  });

  it('resumes today progress from the day queue after re-entering (PRD ch.8)', async () => {
    const user = userEvent.setup();
    const resumedCard = { ...studyCard(), queuePosition: 11 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
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
      });
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
});

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
