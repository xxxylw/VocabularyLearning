import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { StudyCard } from '../api';
import { SpellingSession } from './SpellingSession';

const spellingCards: StudyCard[] = [
  {
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
    queueType: 'new',
    degraded: false
  },
  {
    cardId: 'card-2',
    cardIds: ['card-2'],
    word: 'carbon dioxide',
    partOfSpeech: 'noun',
    senseLabel: 'gas',
    definition: 'a gas produced when carbon burns',
    definitionSource: 'oxford_api',
    examples: [],
    chineseNote: null,
    senses: [],
    queueType: 'review',
    degraded: false
  }
];

const hydrogenCard: StudyCard = {
  cardId: 'card-h',
  cardIds: ['card-h'],
  word: 'hydrogen',
  partOfSpeech: 'noun',
  senseLabel: 'chemical element',
  definition: 'a chemical element. Hydrogen is a gas that is the lightest of all the elements.',
  definitionSource: 'oxford_api',
  examples: [],
  chineseNote: null,
  senses: [],
  queueType: 'new',
  degraded: false
};

describe('SpellingSession', () => {
  it('never leaks the answer through the prompt and checks spelling with normalized spacing and case', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    // F-01: the chinese note ("El Nino phenomenon") contains the answer, so
    // the prompt must fall through to the safe definition sentence instead.
    expect(screen.queryByText('El Nino phenomenon')).not.toBeInTheDocument();
    expect(screen.getByText('a weather pattern that warms the eastern Pacific Ocean')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), '  el   nino ');
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/correct/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /next/i }));

    // F-01: every sentence of the "carbon dioxide" definition leaks a
    // component of the answer, so the prompt falls back to a structured
    // hint instead of revealing "carbon".
    expect(screen.getByText('2 words · 13 letters · starts with "c" · ends with "e"')).toBeInTheDocument();
    expect(screen.queryByText(/a gas produced when carbon burns/i)).not.toBeInTheDocument();
  });

  it('drops the leaking sentence and shows only the safe fragment (hydrogen)', async () => {
    render(<SpellingSession cards={[hydrogenCard]} onExit={vi.fn()} />);

    expect(screen.getByRole('heading', { level: 1, name: /a chemical element/i })).toBeInTheDocument();
    // The whole document must not contain the answer before it is revealed.
    expect(screen.queryByText(/hydrogen/i)).not.toBeInTheDocument();
  });

  it('matches the answer case-insensitively when building the prompt', () => {
    render(
      <SpellingSession
        cards={[{ ...hydrogenCard, definition: 'HYDROGEN is the lightest gas. A chemical element.' }]}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { level: 1, name: /a chemical element/i })).toBeInTheDocument();
    expect(screen.queryByText(/hydrogen/i)).not.toBeInTheDocument();
  });

  it('shows retry feedback and can reveal the answer for a wrong spelling', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), 'El Nnio');
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/try again/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show answer/i }));

    expect(screen.getByText(/^Answer: El Nino$/)).toBeInTheDocument();
  });

  it('only shows pronunciation after the spelling answer is revealed', async () => {
    const user = userEvent.setup();
    const onLookupPronunciation = vi.fn().mockResolvedValue({
      word: 'El Nino',
      ipa: '/ɛl ˈninjoʊ/',
      audioUrl: 'https://upload.wikimedia.org/el-nino.ogg',
      sourceUrl: 'https://en.wiktionary.org/wiki/El_Nino#English',
      audioSourceUrl: 'https://commons.wikimedia.org/wiki/File:el-nino.ogg',
      attribution: 'Wikimedia Commons contributor',
      license: 'CC BY-SA 4.0',
      licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/',
      status: 'ready'
    });

    render(
      <SpellingSession cards={spellingCards} onExit={vi.fn()} onLookupPronunciation={onLookupPronunciation} />
    );

    expect(onLookupPronunciation).not.toHaveBeenCalled();
    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), 'wrong');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /show answer/i }));

    expect(await screen.findByText('/ɛl ˈninjoʊ/ US')).toBeInTheDocument();
  });

  it('returns home from the completion screen', async () => {
    const user = userEvent.setup();
    const onExit = vi.fn();
    render(<SpellingSession cards={[spellingCards[0]]} onExit={onExit} />);

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), 'El Nino');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await user.click(screen.getByRole('button', { name: /back home/i }));

    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it('resumes day-queue progress from the shared snapshot offset (PRD ch.8)', async () => {
    const user = userEvent.setup();
    render(
      <SpellingSession
        cards={spellingCards}
        startIndex={10}
        totalCount={40}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByText('11 / 40')).toBeInTheDocument();
    expect(screen.getByText('10 / 40 completed')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), 'El Nino');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.getByText('12 / 40')).toBeInTheDocument();
    expect(screen.getByText('11 / 40 completed')).toBeInTheDocument();
  });


  it('does not apply the single-line font fit to the spelling prompt (PRD ch.12)', () => {
    // Even with the same overflow measurements that would shrink a study
    // card headline, the spelling prompt keeps its default responsive size.
    // A real CSSStyleDeclaration keeps methods (getPropertyValue, ...) that
    // user-event and other libraries call on computed styles.
    const computedStyle = document.createElement('div').style;
    computedStyle.fontSize = '48px';
    vi.spyOn(window, 'getComputedStyle').mockReturnValue(computedStyle);
    vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockReturnValue(900);
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(300);

    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    const prompt = screen.getByRole('heading', { level: 1, name: /a weather pattern that warms/i });
    expect(prompt.className).not.toContain('word-headline');
    expect(prompt.style.fontSize).toBe('');

    vi.restoreAllMocks();
  });
});
