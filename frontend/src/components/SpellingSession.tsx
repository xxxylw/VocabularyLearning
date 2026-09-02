import { useState } from 'react';
import type { StudyCard } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';

type SpellingSessionProps = {
  cards: StudyCard[];
  onExit: () => void;
  onLookupPronunciation?: (word: string) => Promise<Pronunciation>;
};

type ResultState = 'idle' | 'correct' | 'incorrect';

function normalizeAnswer(value: string): string {
  return value
    .replace(/[‘’]/g, "'")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

function promptFor(card: StudyCard): string {
  const firstSense = card.senses[0];

  return card.chineseNote
    ?? firstSense?.chineseNote
    ?? card.definition
    ?? firstSense?.definition
    ?? '';
}

export function SpellingSession({ cards, onExit, onLookupPronunciation }: SpellingSessionProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState<ResultState>('idle');
  const [showAnswer, setShowAnswer] = useState(false);

  const card = cards[currentIndex];
  const completedCount = Math.min(currentIndex, cards.length);
  const isComplete = cards.length === 0 || currentIndex >= cards.length;

  if (isComplete || !card) {
    return (
      <main className="spelling-shell completion-state" aria-label="Spelling complete">
        <section className="completion-panel" aria-labelledby="spelling-complete-title">
          <p className="eyebrow">Spelling complete</p>
          <h1 id="spelling-complete-title">Spelling pass finished.</h1>
          <p>{cards.length} words checked. Nice clean finish.</p>
          <button className="primary-action" type="button" onClick={onExit}>
            Back home
          </button>
        </section>
      </main>
    );
  }

  const prompt = promptFor(card);
  const expectedAnswer = normalizeAnswer(card.word);
  const isCorrect = result === 'correct';

  function handleCheck() {
    setShowAnswer(false);
    setResult(normalizeAnswer(answer) === expectedAnswer ? 'correct' : 'incorrect');
  }

  function handleNext() {
    setCurrentIndex((index) => index + 1);
    setAnswer('');
    setResult('idle');
    setShowAnswer(false);
  }

  return (
    <main className="spelling-shell" aria-label="Spelling practice">
      <header className="study-topbar">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="progress-text">
          {currentIndex + 1} / {cards.length}
        </div>
      </header>

      <section className="spelling-card" aria-labelledby="spelling-title">
        <div className="spelling-prompt">
          <p className="eyebrow">Definition prompt</p>
          <h1 id="spelling-title">{prompt}</h1>
          <p>{card.partOfSpeech}{card.senseLabel ? ` · ${card.senseLabel}` : ''}</p>
        </div>

        <div className="spelling-answer-panel">
          <label htmlFor="spelling-answer">Type the English word</label>
          <input
            id="spelling-answer"
            className="spelling-input"
            value={answer}
            onChange={(event) => {
              setAnswer(event.target.value);
              setResult('idle');
              setShowAnswer(false);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                if (isCorrect) {
                  handleNext();
                } else {
                  handleCheck();
                }
              }
            }}
            autoFocus
          />

          <div className="spelling-actions">
            <button className="primary-action" type="button" onClick={isCorrect ? handleNext : handleCheck}>
              {isCorrect ? 'Next' : 'Check'}
            </button>
            {result === 'incorrect' ? (
              <button className="ghost-button" type="button" onClick={() => setShowAnswer(true)}>
                Show answer
              </button>
            ) : null}
          </div>

          {result === 'correct' ? <p className="spelling-feedback correct">Correct.</p> : null}
          {result === 'incorrect' ? <p className="spelling-feedback incorrect">Try again.</p> : null}
          {showAnswer ? <p className="spelling-answer">Answer: {card.word}</p> : null}
          {showAnswer && onLookupPronunciation ? (
            <PronunciationPanel word={card.word} onLookupPronunciation={onLookupPronunciation} />
          ) : null}
          <p className="completed-text">{completedCount} / {cards.length} completed</p>
        </div>
      </section>
    </main>
  );
}
