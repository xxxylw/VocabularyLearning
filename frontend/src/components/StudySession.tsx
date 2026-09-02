import { useCallback, useEffect, useRef, useState } from 'react';
import type { StudyCard, DefinitionSource } from '../api';
import type { OxfordLookupResult } from '../api';
import type { ReviewRating } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';

type StudySessionProps = {
  cards: StudyCard[];
  onReview: (card: StudyCard, rating: ReviewRating) => Promise<unknown> | unknown;
  onExit: () => void;
  onLookupWord?: (word: string) => Promise<OxfordLookupResult>;
  onLookupPronunciation?: (word: string) => Promise<Pronunciation>;
  onComplete?: (cards: StudyCard[]) => void;
  onPracticeSpelling?: (cards: StudyCard[]) => void;
  onReviewDueWords?: (cards: StudyCard[]) => void;
};

const ratingLabels: Array<{ rating: ReviewRating; label: string; shortcut: string }> = [
  { rating: 'known', label: 'Got it', shortcut: '1' },
  { rating: 'uncertain', label: 'Maybe', shortcut: '2' },
  { rating: 'unknown', label: 'New', shortcut: '3' }
];

const VISIBLE_SENSE_COUNT = 3;
const DEGRADED_SOURCES: ReadonlySet<DefinitionSource> = new Set(['fallback']);

function isDistinctSenseLabel(senseLabel: string, definition: string): boolean {
  return senseLabel.trim().toLowerCase() !== definition.trim().toLowerCase();
}

function isDegradedSource(source: DefinitionSource | undefined): boolean {
  return source !== undefined && DEGRADED_SOURCES.has(source);
}

type LookupState =
  | { status: 'idle' }
  | { status: 'loading'; word: string }
  | { status: 'ready'; result: OxfordLookupResult }
  | { status: 'error'; word: string; message: string };

export function StudySession({
  cards,
  onReview,
  onExit,
  onLookupWord,
  onLookupPronunciation,
  onComplete,
  onPracticeSpelling,
  onReviewDueWords
}: StudySessionProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isRevealed, setIsRevealed] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupState, setLookupState] = useState<LookupState>({ status: 'idle' });
  const [showAllDefinitions, setShowAllDefinitions] = useState(false);
  const submittingRef = useRef(false);
  const completedRef = useRef(false);

  const card = cards[currentIndex];
  const completedCount = Math.min(currentIndex, cards.length);
  const completionPercent = cards.length === 0 ? 0 : (completedCount / cards.length) * 100;
  const isComplete = cards.length > 0 && currentIndex >= cards.length;
  const newCardsCompleted = cards.filter((item) => item.queueType === 'new').length;
  const reviewCardsCompleted = cards.filter((item) => item.queueType === 'review').length;
  const reviewCards = cards.filter((item) => item.queueType === 'review');

  const handleRating = useCallback(
    async (rating: ReviewRating) => {
      if (!card || submittingRef.current) {
        return;
      }

      submittingRef.current = true;
      setIsSubmitting(true);
      setError(null);

      try {
        await onReview(card, rating);
        setCurrentIndex((index) => index + 1);
        setIsRevealed(false);
        setLookupState({ status: 'idle' });
        setShowAllDefinitions(false);
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

      if (!isRevealed && (event.key === ' ' || event.code === 'Space' || event.key === 'Enter')) {
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

  useEffect(() => {
    if (!isComplete || completedRef.current) {
      return;
    }

    completedRef.current = true;
    onComplete?.(cards);
  }, [cards, isComplete, onComplete]);

  if (!card) {
    return (
      <main
        className="study-shell completion-state"
        aria-label="Session complete"
        onClick={onExit}
      >
        <div className="completion-panel" aria-labelledby="completion-title">
          <div className="celebration-field" aria-hidden="true">
            {Array.from({ length: 18 }, (_, index) => (
              <span key={index} />
            ))}
          </div>
          <div className="completion-mark" aria-hidden="true">
            <span>OK</span>
          </div>
          <p className="eyebrow">Session complete</p>
          <h1 id="completion-title">Checked in for today.</h1>
          <p>The desk is clear. Tomorrow&apos;s review path is already waiting in the background.</p>
          <div className="completion-stats" aria-label="Completed session summary">
            <div>
              <strong>{cards.length}</strong>
              <span>cards</span>
            </div>
            <div>
              <strong>{newCardsCompleted}</strong>
              <span>new</span>
            </div>
            <div>
              <strong>{reviewCardsCompleted}</strong>
              <span>review</span>
            </div>
          </div>
          <div className="completion-actions">
            {onReviewDueWords && reviewCards.length > 0 ? (
              <button
                className="secondary-action"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onReviewDueWords(reviewCards);
                }}
              >
                Review due words
              </button>
            ) : null}
            {onPracticeSpelling ? (
              <button
                className="primary-action"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onPracticeSpelling(cards);
                }}
              >
                Practice spelling
              </button>
            ) : null}
            <button
              className="ghost-button completion-exit-button"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onExit();
              }}
            >
              Back home
            </button>
          </div>
        </div>
      </main>
    );
  }

  // When a card is "degraded" (senses came from the fallback provider) the
  // server still ships the placeholder text, but the frontend must NOT show
  // it as a real definition. We always render a "Definition preparing"
  // notice and only show the first example if it isn't template text.
  const senses = card.senses.length > 0
    ? card.senses
    : [
        {
          cardId: card.cardId,
          partOfSpeech: card.partOfSpeech,
          senseLabel: card.senseLabel,
          definition: card.definition,
          definitionSource: card.definitionSource,
          examples: card.examples,
          chineseNote: card.chineseNote
        }
      ];

  const cardIsDegraded = card.degraded || senses.every((sense) => isDegradedSource(sense.definitionSource));
  const hiddenSenseCount = Math.max(senses.length - VISIBLE_SENSE_COUNT, 0);
  const visibleSenses = showAllDefinitions || hiddenSenseCount === 0
    ? senses
    : senses.slice(0, VISIBLE_SENSE_COUNT);

  async function handleRatingClick(rating: ReviewRating) {
    if (isSubmitting) {
      return;
    }

    await handleRating(rating);
  }

  async function handleLookupSelection() {
    if (!onLookupWord) {
      return;
    }

    const selectedText = window.getSelection()?.toString() ?? '';
    const selectedWord = selectedText.match(/[A-Za-z][A-Za-z'-]*/)?.[0]?.toLowerCase();

    if (!selectedWord) {
      return;
    }

    setLookupState({ status: 'loading', word: selectedWord });

    try {
      const result = await onLookupWord(selectedWord);
      setLookupState({ status: 'ready', result });
    } catch {
      setLookupState({
        status: 'error',
        word: selectedWord,
        message: 'Oxford did not return a definition for this word.'
      });
    }
  }

  return (
    <main className="study-shell" aria-label="Study session">
      <header className="study-topbar">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="study-progress">
          <div className="progress-text" aria-label="Progress">
            {currentIndex + 1} / {cards.length}
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-label="Today completed words"
            aria-valuemin={0}
            aria-valuemax={cards.length}
            aria-valuenow={completedCount}
          >
            <div className="progress-bar-fill" style={{ width: `${completionPercent}%` }} />
          </div>
          <div className="completed-text">{completedCount} / {cards.length} completed</div>
        </div>
      </header>

      <section className="study-card" aria-labelledby="study-word">
        <div className="queue-pill">{card.queueType}</div>
        <div className="card-front">
          <h1 id="study-word" className="word-headline">{card.word}</h1>
          <div className="phonetics-slot" aria-label="Pronunciation placeholder">
            <span className="phonetics-placeholder">Pronunciation · coming in v2</span>
            {onLookupPronunciation ? (
              <PronunciationPanel word={card.word} onLookupPronunciation={onLookupPronunciation} mode="placeholder" />
            ) : null}
          </div>
        </div>

        {!isRevealed ? (
          <button className="primary-action reveal-action" type="button" onClick={() => setIsRevealed(true)}>
            Reveal
          </button>
        ) : (
          <div className="card-back">
            {cardIsDegraded ? (
              <div className="definition-preparing" role="status" aria-live="polite">
                <strong>Definition preparing</strong>
                <span>Real Oxford content will replace this entry shortly. Tap Reveal later to check again.</span>
              </div>
            ) : null}

            <div className="sense-list" onDoubleClick={() => void handleLookupSelection()}>
              {visibleSenses.map((sense, index) => {
                const primaryExample = sense.examples.find((example) => example.isPrimary) ?? sense.examples[0];
                const shouldShowSenseLabel =
                  sense.senseLabel && isDistinctSenseLabel(sense.senseLabel, sense.definition);
                const isPrimary = index === 0;
                const senseIsDegraded = isDegradedSource(sense.definitionSource);

                return (
                  <section
                    className={`sense-card${isPrimary ? ' is-primary-sense' : ''}`}
                    key={sense.cardId}
                    aria-label={`Sense ${index + 1}`}
                  >
                    <div className="definition-block">
                      {sense.partOfSpeech ? (
                        <span className="pos-badge" aria-label="Part of speech">
                          {sense.partOfSpeech}
                        </span>
                      ) : null}
                      {shouldShowSenseLabel ? <strong className="sense-label">{sense.senseLabel}</strong> : null}
                      {senseIsDegraded ? (
                        <p className="definition-text definition-text-placeholder">Definition preparing</p>
                      ) : (
                        <p className={isPrimary ? 'definition-text definition-text-primary' : 'definition-text'}>
                          {sense.definition}
                        </p>
                      )}
                    </div>

                    {primaryExample && !senseIsDegraded ? (
                      <div className="example-block">
                        <p>{primaryExample.sentence}</p>
                      </div>
                    ) : null}

                    {sense.chineseNote ? <p className="chinese-note">{sense.chineseNote}</p> : null}
                  </section>
                );
              })}

              {!showAllDefinitions && hiddenSenseCount > 0 ? (
                <button
                  type="button"
                  className="show-more-definitions"
                  onClick={() => setShowAllDefinitions(true)}
                  aria-expanded={false}
                >
                  Show more definitions ({hiddenSenseCount})
                </button>
              ) : null}
            </div>

            {lookupState.status !== 'idle' ? (
              <aside className="lookup-popover" role="dialog" aria-label="Oxford lookup" aria-live="polite">
                <div className="lookup-popover-header">
                  <span>Oxford lookup</span>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Close lookup"
                    onClick={() => setLookupState({ status: 'idle' })}
                  >
                    x
                  </button>
                </div>

                {lookupState.status === 'loading' ? <p>Looking up {lookupState.word}...</p> : null}

                {lookupState.status === 'error' ? (
                  <p>
                    {lookupState.message} <strong>{lookupState.word}</strong>
                  </p>
                ) : null}

                {lookupState.status === 'ready' ? (
                  <div>
                    <h2>{lookupState.result.word}</h2>
                    <div className="lookup-senses">
                      {lookupState.result.senses.map((sense, index) => (
                        <section className="lookup-sense" key={`${sense.definition}-${index}`}>
                          <span>{sense.partOfSpeech}</span>
                          <p>{sense.definition}</p>
                        </section>
                      ))}
                    </div>
                    <a href={lookupState.result.sourceUrl} target="_blank" rel="noreferrer">
                      Open in Oxford
                    </a>
                  </div>
                ) : null}
              </aside>
            ) : null}

            <div className="rating-row" aria-label="Rate this card">
              {ratingLabels.map((item) => (
                <button
                  className={`rating-button rating-${item.rating}`}
                  type="button"
                  key={item.rating}
                  onClick={() => void handleRatingClick(item.rating)}
                  disabled={isSubmitting}
                  aria-keyshortcuts={item.shortcut}
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
