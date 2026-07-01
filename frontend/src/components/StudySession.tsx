import { useCallback, useEffect, useRef, useState } from 'react';
import type { StudyCard } from '../api';
import type { ReviewRating } from '../api';

type StudySessionProps = {
  cards: StudyCard[];
  onReview: (cardId: string, rating: ReviewRating) => Promise<unknown> | unknown;
  onExit: () => void;
};

const ratingLabels: Array<{ rating: ReviewRating; label: string }> = [
  { rating: 'known', label: 'Known' },
  { rating: 'uncertain', label: 'Uncertain' },
  { rating: 'unknown', label: 'Unknown' }
];

export function StudySession({ cards, onReview, onExit }: StudySessionProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isRevealed, setIsRevealed] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submittingRef = useRef(false);

  const card = cards[currentIndex];

  const handleRating = useCallback(
    async (rating: ReviewRating) => {
      if (!card || submittingRef.current) {
        return;
      }

      submittingRef.current = true;
      setIsSubmitting(true);
      setError(null);

      try {
        await onReview(card.cardId, rating);
        setCurrentIndex((index) => index + 1);
        setIsRevealed(false);
      } catch {
        setError('The review did not save. Try that rating again.');
      } finally {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    },
    [card, onReview]
  );

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!card) {
        return;
      }

      if (!isRevealed && (event.key === ' ' || event.code === 'Space')) {
        event.preventDefault();
        setIsRevealed(true);
        return;
      }

      if (!isRevealed || submittingRef.current) {
        return;
      }

      const shortcutRatings: Record<string, ReviewRating> = {
        '1': 'known',
        '2': 'uncertain',
        '3': 'unknown'
      };
      const rating = shortcutRatings[event.key];

      if (rating) {
        event.preventDefault();
        void handleRating(rating);
      }
    }

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [card, handleRating, isRevealed]);

  if (!card) {
    return (
      <main className="study-shell completion-state">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="completion-panel">
          <p className="eyebrow">Session complete</p>
          <h1>Today&apos;s cards are finished.</h1>
          <p>The desk is clear. Come back tomorrow for the next round.</p>
        </div>
      </main>
    );
  }

  const primaryExample = card.examples.find((example) => example.isPrimary) ?? card.examples[0];

  async function handleRatingClick(rating: ReviewRating) {
    if (isSubmitting) {
      return;
    }

    await handleRating(rating);
  }

  return (
    <main className="study-shell" aria-label="Study session">
      <header className="study-topbar">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="progress-text" aria-label="Progress">
          {currentIndex + 1} / {cards.length}
        </div>
      </header>

      <section className="study-card" aria-labelledby="study-word">
        <div className="queue-pill">{card.queueType}</div>
        <div className="card-front">
          <p className="part-of-speech">{card.partOfSpeech}</p>
          <h1 id="study-word">{card.word}</h1>
          <p className="sense-label">{card.senseLabel}</p>
        </div>

        {!isRevealed ? (
          <button className="primary-action reveal-action" type="button" onClick={() => setIsRevealed(true)}>
            Reveal
          </button>
        ) : (
          <div className="card-back">
            <div className="definition-block">
              <span>Definition</span>
              <p>{card.definition}</p>
            </div>

            {primaryExample ? (
              <div className="example-block">
                <span>IELTS example</span>
                <p>{primaryExample.sentence}</p>
              </div>
            ) : null}

            {card.chineseNote ? <p className="chinese-note">{card.chineseNote}</p> : null}

            <div className="rating-row" aria-label="Rate this card">
              {ratingLabels.map((item) => (
                <button
                  className={`rating-button rating-${item.rating}`}
                  type="button"
                  key={item.rating}
                  onClick={() => void handleRatingClick(item.rating)}
                  disabled={isSubmitting}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {error ? <p className="inline-error">{error}</p> : null}
          </div>
        )}
      </section>
    </main>
  );
}
