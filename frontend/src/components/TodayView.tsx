type TodayViewProps = {
  onStart: () => void;
  isLoading: boolean;
  error?: string | null;
};

export function TodayView({ onStart, isLoading, error }: TodayViewProps) {
  return (
    <section className="today-view" aria-labelledby="today-title">
      <div className="today-copy">
        <p className="eyebrow">Today</p>
        <h1 id="today-title">Settle into a focused vocabulary round.</h1>
        <p className="today-note">
          A quiet desk, a short queue, and no clutter between you and the next word.
        </p>
      </div>

      <div className="desk-panel" aria-label="Study desk summary">
        <div className="stat-row">
          <span>New word target</span>
          <strong>20</strong>
        </div>
        <div className="stat-row">
          <span>Mode</span>
          <strong>Today cards</strong>
        </div>
        <div className="stat-row">
          <span>Rhythm</span>
          <strong>Reveal, rate, continue</strong>
        </div>
        <button className="primary-action" type="button" onClick={onStart} disabled={isLoading}>
          {isLoading ? 'Preparing cards' : 'Start today cards'}
        </button>
        {error ? <p className="inline-error">{error}</p> : null}
      </div>
    </section>
  );
}
