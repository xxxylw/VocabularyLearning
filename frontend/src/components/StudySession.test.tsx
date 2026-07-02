import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StudySession } from './StudySession';
import type { StudyCard } from '../api';

const cards: StudyCard[] = [
  {
    cardId: 'card-1',
    cardIds: ['card-1', 'card-2'],
    word: 'atmosphere',
    partOfSpeech: 'noun',
    senseLabel: 'air around the earth',
    definition: 'the mixture of gases that surrounds the earth',
    examples: [
      {
        exampleId: 'example-1',
        sentence: 'The atmosphere protects life from harmful solar radiation.',
        isPrimary: true
      }
    ],
    chineseNote: '中文备注：大气；气氛。',
    senses: [
      {
        cardId: 'card-1',
        partOfSpeech: 'noun',
        senseLabel: 'air around the earth',
        definition: 'the mixture of gases that surrounds the earth',
        examples: [
          {
            exampleId: 'example-1',
            sentence: 'The atmosphere protects life from harmful solar radiation.',
            isPrimary: true
          }
        ],
        chineseNote: '中文备注：大气；气氛。'
      },
      {
        cardId: 'card-2',
        partOfSpeech: 'noun',
        senseLabel: 'mood in a place',
        definition: 'the feeling or mood in a place or situation',
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
    queueType: 'new'
  }
];

describe('StudySession', () => {
  it('shows only the word front and Reveal button before revealing the back', () => {
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    expect(screen.getByText('atmosphere')).toBeInTheDocument();
    expect(screen.getByText(/noun/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reveal/i })).toBeInTheDocument();
    expect(screen.queryByText(/mixture of gases/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/air around the earth/i)).not.toBeInTheDocument();
  });

  it('reveals every definition, its IELTS example, Chinese note, and feedback buttons', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    expect(screen.getByText(/mixture of gases that surrounds the earth/i)).toBeInTheDocument();
    expect(screen.getByText(/protects life from harmful solar radiation/i)).toBeInTheDocument();
    expect(screen.getByText(/feeling or mood in a place/i)).toBeInTheDocument();
    expect(screen.getByText(/calm classroom atmosphere/i)).toBeInTheDocument();
    expect(screen.getByText('中文备注：大气；气氛。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^known$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^uncertain$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^unknown$/i })).toBeInTheDocument();
  });

  it('calls the review handler with the whole word card and rating known', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /^known$/i }));

    expect(onReview).toHaveBeenCalledWith(cards[0], 'known');
  });

  it('reveals the card back when Space is pressed', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.keyboard('[Space]');

    expect(screen.getByText(/mixture of gases that surrounds the earth/i)).toBeInTheDocument();
    expect(screen.getByText(/calm classroom atmosphere/i)).toBeInTheDocument();
  });

  it('submits known with the 1 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('1');

    expect(onReview).toHaveBeenCalledWith(cards[0], 'known');
  });

  it('submits uncertain with the 2 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('2');

    expect(onReview).toHaveBeenCalledWith(cards[0], 'uncertain');
  });

  it('submits unknown with the 3 key after reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('[Space]');
    await user.keyboard('3');

    expect(onReview).toHaveBeenCalledWith(cards[0], 'unknown');
  });

  it('does not submit a rating shortcut before reveal', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.keyboard('1');

    expect(onReview).not.toHaveBeenCalled();
  });
});
