import { useState } from 'react';
import { exportFullBook, reviewCard, startTodaySession } from './api';
import type { StudyCard } from './api';
import { ExportView } from './components/ExportView';
import { StudySession } from './components/StudySession';
import { TodayView } from './components/TodayView';

type Screen = 'today' | 'study' | 'empty';

export function App() {
  const [screen, setScreen] = useState<Screen>('today');
  const [cards, setCards] = useState<StudyCard[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newWordTarget, setNewWordTarget] = useState(20);

  async function handleStart(target: number) {
    setIsLoading(true);
    setError(null);

    try {
      const session = await startTodaySession(target);
      setCards(session.cards);
      setScreen(session.cards.length > 0 ? 'study' : 'empty');
    } catch {
      setError('Today cards could not be loaded. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }

  if (screen === 'study') {
    return <StudySession cards={cards} onReview={reviewCard} onExit={() => setScreen('today')} />;
  }

  return (
    <main className="app-shell">
      <TodayView
        onStart={(target) => void handleStart(target)}
        isLoading={isLoading}
        newWordTarget={newWordTarget}
        onNewWordTargetChange={setNewWordTarget}
        error={error}
      />
      {screen === 'empty' ? (
        <section className="empty-state" aria-live="polite">
          <p className="eyebrow">All clear</p>
          <h2>No cards are waiting today.</h2>
          <p>Enjoy the lighter day. The next due review will appear here when it is ready.</p>
        </section>
      ) : null}
      <ExportView onExport={exportFullBook} />
    </main>
  );
}
