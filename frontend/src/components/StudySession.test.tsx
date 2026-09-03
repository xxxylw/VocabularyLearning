import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StudySession } from './StudySession';
import type { StudyCard } from '../api';

const baseCard: StudyCard = {
  cardId: 'card-1',
  cardIds: ['card-1', 'card-2'],
  word: 'atmosphere',
  partOfSpeech: 'noun',
  senseLabel: 'air around the earth',
  definition: 'the mixture of gases that surrounds the earth',
  definitionSource: 'oxford_api',
  examples: [
    {
      exampleId: 'example-1',
      sentence: 'The atmosphere protects life from harmful solar radiation.',
      isPrimary: true
    }
  ],
  chineseNote: 'Air around the earth; the mood of a place.',
  senses: [
    {
      cardId: 'card-1',
      partOfSpeech: 'noun',
      senseLabel: 'air around the earth',
      definition: 'the mixture of gases that surrounds the earth',
      definitionSource: 'oxford_api',
      examples: [
        {
          exampleId: 'example-1',
          sentence: 'The atmosphere protects life from harmful solar radiation.',
          isPrimary: true
        },
        {
          exampleId: 'example-1b',
          sentence: 'These factories are releasing toxic gases into the atmosphere.',
          isPrimary: false
        }
      ],
      chineseNote: 'Air around the earth; the mood of a place.'
    },
    {
      cardId: 'card-2',
      partOfSpeech: 'noun',
      senseLabel: 'mood in a place',
      definition: 'the feeling or mood in a place or situation',
      definitionSource: 'oxford_api',
      examples: [
        {
          exampleId: 'example-2',
          sentence: 'A calm classroom atmosphere can improve concentration.',
          isPrimary: true
        }
      ],
      chineseNote: null
    }
  ],
  queueType: 'new',
  degraded: false
};

const cards: StudyCard[] = [baseCard];

const twoCards: StudyCard[] = [
  baseCard,
  {
    ...baseCard,
    cardId: 'card-3',
    cardIds: ['card-3'],
    word: 'altitude',
    definition: 'the height above sea level',
    examples: [
      {
        exampleId: 'example-3',
        sentence: 'The town is located at a high altitude.',
        isPrimary: true
      }
    ],
    senses: [
      {
        ...baseCard.senses[0],
        cardId: 'card-3',
        senseLabel: 'height above sea level',
        definition: 'the height above sea level',
        examples: [
          {
            exampleId: 'example-3',
            sentence: 'The town is located at a high altitude.',
            isPrimary: true
          }
        ],
        chineseNote: null
      }
    ]
  }
];

describe('StudySession', () => {
  it('shows only the word headline and Reveal button before revealing the back', () => {
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    expect(screen.getByRole('heading', { level: 1, name: 'atmosphere' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reveal/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /got it/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /maybe/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^new$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/mixture of gases/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/air around the earth/i)).not.toBeInTheDocument();
  });

  it('renders real UK and US IPA from the pronunciation API', async () => {
    const onLookupPronunciation = vi.fn().mockResolvedValue({
      word: 'atmosphere',
      ipa: null,
      ipaUk: '/ˈætməsfɪə(r)/',
      ipaUs: '/ˈætməsfɪr/',
      audioUrl: null,
      sourceUrl: 'https://www.oxfordlearnersdictionaries.com/definition/english/atmosphere',
      status: 'ready'
    });

    render(
      <StudySession
        cards={cards}
        onReview={vi.fn()}
        onExit={vi.fn()}
        onLookupPronunciation={onLookupPronunciation}
      />
    );

    expect(await screen.findByText('/ˈætməsfɪə(r)/ UK')).toBeInTheDocument();
    expect(screen.getByText('/ˈætməsfɪr/ US')).toBeInTheDocument();
    expect(screen.queryByText(/Pronunciation · coming in v2/i)).not.toBeInTheDocument();
    expect(onLookupPronunciation).toHaveBeenCalledWith('atmosphere');
  });

  it('renders no pronunciation slot at all when the lookup has no data', async () => {
    const onLookupPronunciation = vi.fn().mockResolvedValue({
      word: 'atmosphere',
      ipa: null,
      ipaUk: null,
      ipaUs: null,
      audioUrl: null,
      sourceUrl: 'https://www.oxfordlearnersdictionaries.com/definition/english/atmosphere',
      status: 'unavailable'
    });

    render(
      <StudySession
        cards={cards}
        onReview={vi.fn()}
        onExit={vi.fn()}
        onLookupPronunciation={onLookupPronunciation}
      />
    );

    await waitFor(() => expect(onLookupPronunciation).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText(/Pronunciation of atmosphere/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/coming in v2/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pending/i)).not.toBeInTheDocument();
  });

  it('shows today completed-word progress while studying', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={twoCards} onReview={onReview} onExit={vi.fn()} />);

    const progress = screen.getByRole('progressbar', { name: /Today completed words/i });
    expect(progress).toHaveAttribute('aria-valuenow', '0');
    expect(progress).toHaveAttribute('aria-valuemax', '2');
    expect(screen.getByText('0 / 2 completed')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));

    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByText('1 / 2 completed')).toBeInTheDocument();
  });

  it('renders POS badges, first-sense emphasis, every real example per sense, and three rating buttons', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    const primarySense = screen.getByLabelText('Sense 1');
    const primaryDefinition = within(primarySense).getByText(/mixture of gases that surrounds the earth/i);
    expect(primaryDefinition.className).toContain('definition-text-primary');

    expect(within(primarySense).getByText('noun')).toBeInTheDocument();
    expect(within(primarySense).getByText(/protects life from harmful solar radiation/i)).toBeInTheDocument();
    expect(within(primarySense).getByText(/releasing toxic gases into the atmosphere/i)).toBeInTheDocument();

    const secondarySense = screen.getByLabelText('Sense 2');
    const secondaryDefinition = within(secondarySense).getByText(/feeling or mood in a place/i);
    expect(secondaryDefinition.className).not.toContain('definition-text-primary');

    expect(within(secondarySense).queryByText(/calm classroom atmosphere/i)).toBeInTheDocument();

    expect(screen.getByRole('button', { name: /got it/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /maybe/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^new$/i })).toBeInTheDocument();
  });

  it('collapses definitions past three into a Show more button', async () => {
    const user = userEvent.setup();
    const manySensesCard: StudyCard = {
      ...baseCard,
      cardId: 'card-many',
      cardIds: ['card-many'],
      senses: [
        baseCard.senses[0],
        { ...baseCard.senses[0], cardId: 'sense-2', senseLabel: 'sense two', definition: 'second sense' },
        { ...baseCard.senses[0], cardId: 'sense-3', senseLabel: 'sense three', definition: 'third sense' },
        { ...baseCard.senses[0], cardId: 'sense-4', senseLabel: 'sense four', definition: 'fourth sense' },
        { ...baseCard.senses[0], cardId: 'sense-5', senseLabel: 'sense five', definition: 'fifth sense' }
      ]
    };
    render(<StudySession cards={[manySensesCard]} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    expect(screen.getByLabelText('Sense 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Sense 3')).toBeInTheDocument();
    expect(screen.queryByLabelText('Sense 4')).not.toBeInTheDocument();

    const showMore = screen.getByRole('button', { name: /Show more definitions \(2\)/i });
    await user.click(showMore);

    expect(screen.getByLabelText('Sense 4')).toBeInTheDocument();
    expect(screen.getByLabelText('Sense 5')).toBeInTheDocument();
  });

  it('swaps fallback definition text for a "Definition preparing" placeholder', async () => {
    const user = userEvent.setup();
    const fallbackCard: StudyCard = {
      ...baseCard,
      senses: [
        {
          ...baseCard.senses[0],
          definition: 'A learner-friendly IELTS study meaning for \'atmosphere\'.',
          definitionSource: 'fallback',
          examples: [
            {
              exampleId: 'example-fb',
              sentence: 'This is a placeholder example while the real entry is being prepared.',
              isPrimary: true
            }
          ]
        }
      ],
      degraded: true
    };
    render(<StudySession cards={[fallbackCard]} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    expect(screen.getByRole('status', { name: '' })).toHaveTextContent(/Definition preparing/i);
    expect(screen.getAllByText(/Definition preparing/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/learner-friendly IELTS study meaning/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/placeholder example while the real entry is being prepared/i)).not.toBeInTheDocument();
  });

  it('does not repeat a sense label when it matches the definition', async () => {
    const user = userEvent.setup();
    const duplicateDefinitionCard: StudyCard = {
      ...baseCard,
      senseLabel: 'the mixture of gases that surrounds the earth',
      senses: [
        {
          ...baseCard.senses[0],
          senseLabel: 'the mixture of gases that surrounds the earth'
        }
      ]
    };
    render(<StudySession cards={[duplicateDefinitionCard]} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    expect(screen.getAllByText('the mixture of gases that surrounds the earth')).toHaveLength(1);
  });

  it('calls the review handler with the whole word card and rating known', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));

    expect(onReview).toHaveBeenCalledWith(baseCard, 'known');
  });

  it('calls onComplete once and shows the check-in completion screen after the final card', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    const onComplete = vi.fn();
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} onComplete={onComplete} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));

    expect(await screen.findByRole('heading', { name: /checked in for today/i })).toBeInTheDocument();
    expect(screen.getByText('cards')).toBeInTheDocument();
    expect(screen.getByText('review')).toBeInTheDocument();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith(cards);
  });

  it('returns home when the completion page is clicked', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    const onExit = vi.fn();
    render(<StudySession cards={cards} onReview={onReview} onExit={onExit} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('main', { name: /session complete/i }));

    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it('offers spelling practice from the completion screen', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    const onPracticeSpelling = vi.fn();
    render(
      <StudySession
        cards={cards}
        onReview={onReview}
        onExit={vi.fn()}
        onPracticeSpelling={onPracticeSpelling}
      />
    );

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /practice spelling/i }));

    expect(onPracticeSpelling).toHaveBeenCalledWith(cards);
  });

  it('offers due review continuation only when review cards are available', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    const onReviewDueWords = vi.fn();
    const reviewCard: StudyCard = { ...baseCard, queueType: 'review' };
    render(
      <StudySession
        cards={[reviewCard]}
        onReview={onReview}
        onExit={vi.fn()}
        onReviewDueWords={onReviewDueWords}
      />
    );

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));
    await user.click(await screen.findByRole('button', { name: /review due words/i }));

    expect(onReviewDueWords).toHaveBeenCalledWith([reviewCard]);
  });

  it('reveals the card back when Space is pressed', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.keyboard('[Space]');

    expect(screen.getByText(/mixture of gases that surrounds the earth/i)).toBeInTheDocument();
    expect(screen.getByText(/calm classroom atmosphere/i)).toBeInTheDocument();
  });

  it('reveals the card back when Enter is pressed', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.keyboard('[Enter]');

    expect(screen.getByText(/mixture of gases that surrounds the earth/i)).toBeInTheDocument();
    expect(screen.getByText(/calm classroom atmosphere/i)).toBeInTheDocument();
  });

  it('submits known with the 1 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('1');

    expect(onReview).toHaveBeenCalledWith(baseCard, 'known');
  });

  it('submits uncertain with the 2 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('2');

    expect(onReview).toHaveBeenCalledWith(baseCard, 'uncertain');
  });

  it('submits unknown with the 3 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('3');

    expect(onReview).toHaveBeenCalledWith(baseCard, 'unknown');
  });

  it('opens selected-word definitions in a dialog without examples', async () => {
    const user = userEvent.setup();
    const onLookupWord = vi.fn().mockResolvedValue({
      word: 'radiation',
      sourceUrl: 'https://www.oxfordlearnersdictionaries.com/definition/english/radiation?q=radiation',
      senses: [
        {
          partOfSpeech: 'noun',
          definition: 'powerful energy that is sent out in the form of rays or waves',
          example: 'The atmosphere protects life from harmful solar radiation.'
        }
      ]
    });
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} onLookupWord={onLookupWord} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    const example = screen.getByText(/harmful solar radiation/i);
    const textNode = example.firstChild;
    expect(textNode).toBeInstanceOf(Text);
    const sentence = textNode?.textContent ?? '';
    const selectedWordStart = sentence.indexOf('radiation');
    const range = document.createRange();
    range.setStart(textNode as Text, selectedWordStart);
    range.setEnd(textNode as Text, selectedWordStart + 'radiation'.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    fireEvent.doubleClick(example);

    expect(onLookupWord).toHaveBeenCalledWith('radiation');
    const dialog = await screen.findByRole('dialog', { name: /Oxford lookup/i });
    expect(within(dialog).getByText(/powerful energy/i)).toBeInTheDocument();
    expect(within(dialog).queryByText(/harmful solar radiation/i)).not.toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /Oxford/i })).toHaveAttribute(
      'href',
      'https://www.oxfordlearnersdictionaries.com/definition/english/radiation?q=radiation'
    );
  });

  it('does not submit a rating shortcut before reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('1');

    expect(onReview).not.toHaveBeenCalled();
  });

  it('resumes day-queue progress instead of restarting from 1 (PRD ch.8)', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    const resumedCards: StudyCard[] = [
      { ...twoCards[0], queuePosition: 11 },
      { ...twoCards[1], queuePosition: 12 }
    ];
    render(
      <StudySession
        cards={resumedCards}
        totalCards={40}
        reviewedCards={10}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByText('11 / 40')).toBeInTheDocument();
    expect(screen.getByText('10 / 40 completed')).toBeInTheDocument();
    const progress = screen.getByRole('progressbar', { name: /Today completed words/i });
    expect(progress).toHaveAttribute('aria-valuenow', '10');
    expect(progress).toHaveAttribute('aria-valuemax', '40');

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /got it/i }));

    expect(screen.getByText('12 / 40')).toBeInTheDocument();
    expect(screen.getByText('11 / 40 completed')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '11'
    );
  });

  it('falls back to session-local progress when no day anchors are provided', () => {
    render(<StudySession cards={twoCards} onReview={vi.fn()} onExit={vi.fn()} />);

    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.getByText('0 / 2 completed')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '0'
    );
  });
});
