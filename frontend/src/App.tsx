import { useState } from 'react';
import { exportFullBook, getBookProgress, lookupOxfordWord, lookupPronunciation, reviewCard, startTodaySession } from './api';
import type { ReviewRating, StudyCard } from './api';
import { buildCheckInRecord, loadCheckIns, saveCheckIn } from './checkins';
import { ExportView } from './components/ExportView';
import { SpellingSession } from './components/SpellingSession';
import { StudySession } from './components/StudySession';
import { TodayView } from './components/TodayView';

type Screen = 'today' | 'study' | 'spelling' | 'empty';
type EmptyReason = 'no-cards' | 'no-book-words';

export function App() {
  const [screen, setScreen] = useState<Screen>('today');
  const [cards, setCards] = useState<StudyCard[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newWordTarget, setNewWordTarget] = useState(20);
  const [emptyReason, setEmptyReason] = useState<EmptyReason>('no-cards');
  const [checkIns, setCheckIns] = useState(() => loadCheckIns());
  const [lastCompletedCards, setLastCompletedCards] = useState<StudyCard[]>([]);

  async function handleStart(target: number) {
    setIsLoading(true);
    setError(null);

    try {
      const session = await startTodaySession(target);
      setCards(session.cards);
      if (session.cards.length > 0) {
        setScreen('study');
        return;
      }

      const progress = await getBookProgress();
      setEmptyReason(progress.totalWords === 0 ? 'no-book-words' : 'no-cards');
      setScreen('empty');
    } catch {
      setError('Today cards could not be loaded. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }

  async function reviewWordCard(card: StudyCard, rating: ReviewRating) {
    const cardIds = card.cardIds.length > 0 ? card.cardIds : [card.cardId];
    await Promise.all(cardIds.map((cardId) => reviewCard(cardId, rating)));
  }

  function handleSessionComplete(completedCards: StudyCard[]) {
    setLastCompletedCards(completedCards);
    const updatedCheckIns = saveCheckIn(buildCheckInRecord(completedCards));
    setCheckIns(updatedCheckIns);
  }

  function startSpellingPractice(spellingCards: StudyCard[]) {
    setCards(spellingCards);
    setLastCompletedCards(spellingCards);
    setScreen('spelling');
  }

  function startReviewDueWords(reviewCards: StudyCard[]) {
    setCards(reviewCards);
    setScreen('study');
  }

  if (screen === 'study') {
    return (
      <StudySession
        cards={cards}
        onReview={reviewWordCard}
        onExit={() => setScreen('today')}
        onLookupWord={lookupOxfordWord}
        onLookupPronunciation={lookupPronunciation}
        onComplete={handleSessionComplete}
        onPracticeSpelling={startSpellingPractice}
        onReviewDueWords={startReviewDueWords}
      />
    );
  }

  if (screen === 'spelling') {
    return (
      <SpellingSession
        cards={cards}
        onExit={() => setScreen('today')}
        onLookupPronunciation={lookupPronunciation}
      />
    );
  }

  return (
    <main className="app-shell">
      <TodayView
        onStart={(target) => void handleStart(target)}
        isLoading={isLoading}
        newWordTarget={newWordTarget}
        onNewWordTargetChange={setNewWordTarget}
        canPracticeSpelling={lastCompletedCards.length > 0}
        onPracticeSpelling={() => startSpellingPractice(lastCompletedCards)}
        checkIns={checkIns}
        error={error}
      />
      {screen === 'empty' ? (
        <section className="empty-state" aria-live="polite">
          {emptyReason === 'no-book-words' ? (
            <>
              <p className="eyebrow">Book setup</p>
              <h2>No book words imported yet.</h2>
              <p>Import the book word list first, then Start today cards will prepare the next words in order.</p>
            </>
          ) : (
            <>
              <p className="eyebrow">All clear</p>
              <h2>Today&apos;s card queue is clear.</h2>
              <p>New words are done for the day. You can keep it light or run a spelling pass.</p>
              {lastCompletedCards.length > 0 ? (
                <div className="empty-actions">
                  <button
                    className="primary-action"
                    type="button"
                    onClick={() => startSpellingPractice(lastCompletedCards)}
                  >
                    Practice spelling now
                  </button>
                  <button className="ghost-button" type="button" onClick={() => setScreen('today')}>
                    Back home
                  </button>
                </div>
              ) : null}
            </>
          )}
        </section>
      ) : null}
      <ExportView onExport={exportFullBook} />
    </main>
  );
}
