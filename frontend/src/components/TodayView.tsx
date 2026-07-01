import { useEffect, useState } from 'react';

type TodayViewProps = {
  onStart: (newWordTarget: number) => void;
  isLoading: boolean;
  newWordTarget: number;
  onNewWordTargetChange: (newWordTarget: number) => void;
  error?: string | null;
};

export function TodayView({ onStart, isLoading, newWordTarget, onNewWordTargetChange, error }: TodayViewProps) {
  const [targetDraft, setTargetDraft] = useState(String(newWordTarget));

  useEffect(() => {
    setTargetDraft(String(newWordTarget));
  }, [newWordTarget]);

  function handleTargetChange(value: string) {
    setTargetDraft(value);

    const nextTarget = Number.parseInt(value, 10);

    if (Number.isNaN(nextTarget)) {
      return;
    }

    onNewWordTargetChange(Math.min(200, Math.max(1, nextTarget)));
  }

  function handleStart() {
    const nextTarget = Number.parseInt(targetDraft, 10);
    onStart(Number.isNaN(nextTarget) ? newWordTarget : Math.min(200, Math.max(1, nextTarget)));
  }

  return (
    <section className="today-view" aria-labelledby="today-title">
      <div className="today-copy">
        <p className="eyebrow">Today</p>
        <h1 id="today-title">Ready for today&apos;s cards</h1>
        <p className="today-note">
          A quiet desk, a short queue, and a focused pass through the words waiting for you.
        </p>
      </div>

      <div className="desk-panel" aria-label="Study desk summary">
        <div className="stat-row">
          <label htmlFor="new-word-target">New word target</label>
          <input
            id="new-word-target"
            className="target-input"
            type="number"
            min="1"
            max="200"
            step="1"
            value={targetDraft}
            onChange={(event) => handleTargetChange(event.target.value)}
            disabled={isLoading}
          />
        </div>
        <div className="stat-row">
          <span>Mode</span>
          <strong>Today cards</strong>
        </div>
        <div className="stat-row">
          <span>Rhythm</span>
          <strong>Reveal, rate, continue</strong>
        </div>
        <button className="primary-action" type="button" onClick={handleStart} disabled={isLoading}>
          {isLoading ? 'Preparing cards' : 'Start today cards'}
        </button>
        {error ? <p className="inline-error">{error}</p> : null}
      </div>
    </section>
  );
}
