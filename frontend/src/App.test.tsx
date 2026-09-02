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

  it('starts spelling practice from the completed study session', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
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
    expect(screen.getByText('El Nino phenomenon')).toBeInTheDocument();
  });

  it('offers spelling practice when today has no more cards after a completed session', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
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
});

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
