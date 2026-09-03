import { useEffect, useState } from 'react';
import {
  getBookProgress,
  getCurrentBook,
  listBooks,
  lookupOxfordWord,
  lookupPronunciation,
  reviewCard,
  startTodaySession,
  switchBook
} from './api';
import type { BookListItem, ReviewRating, StudyCard } from './api';
import { buildCheckInRecord, loadCheckIns, saveCheckIn } from './checkins';
import { BookShelfView } from './components/BookShelfView';
import { SpellingSession } from './components/SpellingSession';
import { StudySession } from './components/StudySession';
import { TodayView } from './components/TodayView';

type Screen = 'today' | 'study' | 'spelling' | 'empty' | 'bookshelf';
type EmptyReason = 'no-cards' | 'no-book-words';

// PRD ch.8: day-level progress anchors from the Today session — kept
// across screens so both card mode and spelling mode resume the day
// queue's progress instead of restarting from 1.
type DayProgress = {
  totalCards: number;
  reviewedCards: number;
};

export function App() {
  const [screen, setScreen] = useState<Screen>('today');
  const [cards, setCards] = useState<StudyCard[]>([]);
  const [dayProgress, setDayProgress] = useState<DayProgress | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newWordTarget, setNewWordTarget] = useState(20);
  const [emptyReason, setEmptyReason] = useState<EmptyReason>('no-cards');
  const [checkIns, setCheckIns] = useState(() => loadCheckIns());
  const [lastCompletedCards, setLastCompletedCards] = useState<StudyCard[]>([]);
  const [bookTitle, setBookTitle] = useState<string | null>(null);
  // PRD ch.9: cover card data + bookshelf state.
  const [bookTotalWords, setBookTotalWords] = useState<number | null>(null);
  const [bookLearnedWords, setBookLearnedWords] = useState<number | null>(null);
  const [bookFallbackNotice, setBookFallbackNotice] = useState<string | null>(null);
  const [bookshelfBooks, setBookshelfBooks] = useState<BookListItem[]>([]);
  const [isSwitching, setIsSwitching] = useState(false);
  const [bookshelfError, setBookshelfError] = useState<string | null>(null);

  useEffect(() => {
    refreshCurrentBook().catch(() => {
      // Book title is informational; keep the page usable when the
      // endpoint is unavailable (e.g. backend still starting up).
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshCurrentBook() {
    const book = await getCurrentBook();
    setBookTitle(book.title);
    setBookTotalWords(book.totalWords);
    setBookLearnedWords(book.learnedWords ?? null);
    setBookFallbackNotice(book.fallbackNotice ?? null);
  }

  async function openBookShelf() {
    setBookshelfError(null);
    try {
      const list = await listBooks();
      setBookshelfBooks(list.books);
      setScreen('bookshelf');
    } catch {
      setBookshelfError('Bookshelf could not be loaded. Please try again.');
    }
  }

  async function handleSwitchBook(bookId: string) {
    setIsSwitching(true);
    setBookshelfError(null);
    try {
      await switchBook(bookId);
      // PRD ch.9: after switching, Today follows the new book — refresh
      // the cover card data and return to Today with a clean slate (the
      // in-progress session ended; graded reviews stay persisted).
      await refreshCurrentBook();
      setCards([]);
      setDayProgress(null);
      setLastCompletedCards([]);
      setScreen('today');
    } catch {
      setBookshelfError('Switching the book failed. Please try again.');
    } finally {
      setIsSwitching(false);
    }
  }

  async function handleStart(target: number) {
    setIsLoading(true);
    setError(null);

    try {
      const session = await startTodaySession(target);
      setCards(session.cards);
      setDayProgress({
        totalCards: session.totalCards,
        reviewedCards: session.reviewedCards ?? 0
      });
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
    // Ad-hoc re-review subset: progress falls back to session-local
    // counting (PRD ch.8 only anchors Today-started sessions).
    setDayProgress(null);
    setScreen('study');
  }

  if (screen === 'study') {
    return (
      <StudySession
        cards={cards}
        totalCards={dayProgress?.totalCards}
        reviewedCards={dayProgress?.reviewedCards}
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
        startIndex={dayProgress?.reviewedCards ?? 0}
        totalCount={dayProgress?.totalCards ?? cards.length}
        onExit={() => setScreen('today')}
        onLookupPronunciation={lookupPronunciation}
      />
    );
  }

  if (screen === 'bookshelf') {
    return (
      <main className="app-shell">
        <BookShelfView
          books={bookshelfBooks}
          onBack={() => setScreen('today')}
          onSwitch={handleSwitchBook}
          isSwitching={isSwitching}
          error={bookshelfError}
          notice={bookFallbackNotice}
        />
      </main>
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
        bookTitle={bookTitle}
        bookTotalWords={bookTotalWords}
        bookLearnedWords={bookLearnedWords}
        onOpenBookShelf={() => void openBookShelf()}
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
    </main>
  );
}
