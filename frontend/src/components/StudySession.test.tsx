import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StudySession } from './StudySession';
import type { StudyCard } from '../api';

const cards: StudyCard[] = [
  {
    cardId: 'card-1',
    word: 'meticulous',
    partOfSpeech: 'adjective',
    senseLabel: 'careful and precise',
    definition: 'Showing great attention to detail.',
    examples: [
      {
        exampleId: 'example-1',
        sentence: 'The researcher kept meticulous notes during the trial.',
        isPrimary: true
      }
    ],
    chineseNote: '中文备注：费用；收费。',
    queueType: 'new'
  }
];

describe('StudySession', () => {
  it('shows the card front and Reveal button before revealing the back', () => {
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    expect(screen.getByText('meticulous')).toBeInTheDocument();
    expect(screen.getByText(/adjective/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reveal/i })).toBeInTheDocument();
    expect(screen.queryByText(/showing great attention/i)).not.toBeInTheDocument();
  });

  it('reveals definition, IELTS example, Chinese note, and feedback buttons', async () => {
    const user = userEvent.setup();
    render(<StudySession cards={cards} onReview={vi.fn()} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));

    expect(screen.getByText(/showing great attention to detail/i)).toBeInTheDocument();
    expect(screen.getByText(/researcher kept meticulous notes/i)).toBeInTheDocument();
    expect(screen.getByText(/中文备注：费用；收费。/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^known$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^uncertain$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^unknown$/i })).toBeInTheDocument();
  });

  it('calls the review handler with rating known', async () => {
    const user = userEvent.setup();
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /reveal/i }));
    await user.click(screen.getByRole('button', { name: /^known$/i }));

    expect(onReview).toHaveBeenCalledWith('card-1', 'known');
  });
});
