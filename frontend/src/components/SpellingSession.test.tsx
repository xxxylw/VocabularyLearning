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

describe('SpellingSession', () => {
  it('checks spelling against the prompted word with normalized spacing and case', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    expect(screen.getByText('El Nino phenomenon')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), '  el   nino ');
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/correct/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.getByText(/a gas produced when carbon burns/i)).toBeInTheDocument();
  });

  it('shows retry feedback and can reveal the answer for a wrong spelling', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: /type the english word/i }), 'El Nnio');
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/try again/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show answer/i }));

    expect(screen.getByText(/El Nino/)).toBeInTheDocument();
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

    expect(await screen.findByText('/ɛl ˈninjoʊ/')).toBeInTheDocument();
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
});
