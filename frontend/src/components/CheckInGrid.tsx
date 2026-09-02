import type { CheckInRecord } from '../checkins';
import { localDateString } from '../checkins';

type CheckInGridProps = {
  checkIns: CheckInRecord[];
  today?: Date;
};

const DAY_LABELS = ['Mon', 'Wed', 'Fri'];
const VISIBLE_WEEKS = 13;
const DAYS_PER_WEEK = 7;

export function CheckInGrid({ checkIns, today = new Date() }: CheckInGridProps) {
  const checkInsByDate = new Map(checkIns.map((record) => [record.date, record]));
  const days = buildVisibleDays(today);
  const totalDays = checkIns.length;
  const currentStreak = getCurrentStreak(checkInsByDate, today);

  return (
    <section className="checkin-panel" aria-labelledby="checkin-title">
      <div className="checkin-header">
        <div>
          <p className="eyebrow">Daily check-in</p>
          <h2 id="checkin-title">Study rhythm</h2>
        </div>
        <div className="streak-badge" aria-label={`${currentStreak} day streak`}>
          <strong>{currentStreak}</strong>
          <span>day streak</span>
        </div>
      </div>

      <div className="checkin-board" aria-label="Daily study check-ins for the last 13 weeks">
        <div className="checkin-days" aria-hidden="true">
          {DAY_LABELS.map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
        <div className="checkin-grid">
          {days.map((date) => {
            const dateKey = localDateString(date);
            const record = checkInsByDate.get(dateKey);
            const level = record ? activityLevel(record.completedCards) : 0;
            const label = record
              ? `${dateKey}: ${record.completedCards} cards completed`
              : `${dateKey}: no check-in`;

            return (
              <span
                className={`checkin-cell checkin-level-${level}`}
                key={dateKey}
                aria-label={label}
                title={label}
              />
            );
          })}
        </div>
      </div>

      <div className="checkin-footer">
        <span>{totalDays} checked-in days</span>
        <span>Less</span>
        <span className="checkin-cell checkin-level-1" aria-hidden="true" />
        <span className="checkin-cell checkin-level-2" aria-hidden="true" />
        <span className="checkin-cell checkin-level-3" aria-hidden="true" />
        <span className="checkin-cell checkin-level-4" aria-hidden="true" />
        <span>More</span>
      </div>
    </section>
  );
}

function buildVisibleDays(today: Date): Date[] {
  const end = startOfDay(today);
  const start = new Date(end);
  start.setDate(end.getDate() - VISIBLE_WEEKS * DAYS_PER_WEEK + 1);

  return Array.from({ length: VISIBLE_WEEKS * DAYS_PER_WEEK }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
}

function getCurrentStreak(checkInsByDate: Map<string, CheckInRecord>, today: Date): number {
  let streak = 0;
  const cursor = startOfDay(today);

  while (checkInsByDate.has(localDateString(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }

  return streak;
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function activityLevel(completedCards: number): 1 | 2 | 3 | 4 {
  if (completedCards >= 40) {
    return 4;
  }

  if (completedCards >= 20) {
    return 3;
  }

  if (completedCards >= 8) {
    return 2;
  }

  return 1;
}
