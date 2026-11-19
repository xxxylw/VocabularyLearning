import { describe, expect, it } from 'vitest';
import type { CheckInRecord } from './checkins';
import { estimateFinishDays, finishEstimateParts, formatFinishEstimate, recentDailyNewWordSpeed } from './estimate';

const TODAY = new Date(2026, 8, 8); // 2026-09-08 本地时区

function record(date: string, newCards: number): CheckInRecord {
  return {
    date,
    completedCards: newCards,
    newCards,
    reviewCards: 0,
    completedAt: `${date}T20:00:00.000Z`
  };
}

describe('recentDailyNewWordSpeed', () => {
  it('takes the median of newCards across days with records in the last 14 days', () => {
    const checkIns = [
      record('2026-09-06', 30),
      record('2026-09-07', 10),
      record('2026-09-08', 22)
    ];

    expect(recentDailyNewWordSpeed(checkIns, TODAY, 20)).toEqual({ speed: 22, source: 'median' });
  });

  it('ignores check-ins older than 14 days and does not pad missing days with zero', () => {
    const checkIns = [
      record('2026-08-01', 100),
      record('2026-08-24', 100),
      record('2026-08-25', 100),
      record('2026-09-05', 5),
      record('2026-09-06', 15),
      record('2026-09-07', 25)
    ];

    // 只有 09-05/06/07 落进 14 天窗口，中位数 15。
    expect(recentDailyNewWordSpeed(checkIns, TODAY, 20)).toEqual({ speed: 15, source: 'median' });
  });

  it('falls back to the target when fewer than 3 days have records', () => {
    expect(recentDailyNewWordSpeed([record('2026-09-08', 40)], TODAY, 20)).toEqual({
      speed: 20,
      source: 'target'
    });
    expect(recentDailyNewWordSpeed([], TODAY, 20)).toEqual({ speed: 20, source: 'target' });
  });

  it('falls back to the target when the median is zero (pure-review users)', () => {
    const checkIns = [
      record('2026-09-05', 0),
      record('2026-09-06', 0),
      record('2026-09-07', 0),
      record('2026-09-08', 0)
    ];

    expect(recentDailyNewWordSpeed(checkIns, TODAY, 20)).toEqual({ speed: 20, source: 'target' });
  });

  it('supports even sample counts with an averaged median', () => {
    const checkIns = [
      record('2026-09-03', 10),
      record('2026-09-05', 20),
      record('2026-09-07', 30),
      record('2026-09-08', 40)
    ];

    expect(recentDailyNewWordSpeed(checkIns, TODAY, 20)).toEqual({ speed: 25, source: 'median' });
  });
});

describe('estimateFinishDays', () => {
  it('computes ceil(remaining / speed) with the median speed', () => {
    const checkIns = [
      record('2026-09-06', 10),
      record('2026-09-07', 10),
      record('2026-09-08', 10)
    ];

    expect(estimateFinishDays(100, 95, checkIns, 20, TODAY)).toEqual({
      kind: 'estimate',
      remaining: 5,
      speed: 10,
      speedSource: 'median',
      days: 1
    });
  });

  it('ceil-rounds fractional days up', () => {
    const checkIns = [
      record('2026-09-06', 3),
      record('2026-09-07', 3),
      record('2026-09-08', 3)
    ];

    expect(estimateFinishDays(100, 95, checkIns, 20, TODAY)).toMatchObject({ days: 2 });
  });

  it('falls back to the new-word target for fresh users (no check-ins)', () => {
    expect(estimateFinishDays(200, 0, [], 20, TODAY)).toMatchObject({
      speed: 20,
      speedSource: 'target',
      days: 10
    });
  });

  it('returns done when there are no remaining new words', () => {
    expect(estimateFinishDays(100, 100, [], 20, TODAY)).toEqual({ kind: 'done' });
    expect(estimateFinishDays(100, 120, [], 20, TODAY)).toEqual({ kind: 'done' });
  });

  it('returns unavailable when the book has no words imported', () => {
    expect(estimateFinishDays(0, 0, [], 20, TODAY)).toEqual({ kind: 'unavailable' });
  });
});

describe('formatFinishEstimate', () => {
  it('appends the English finish date when days <= 30', () => {
    const estimate = estimateFinishDays(100, 80, [], 20, TODAY);
    // 剩 20 词、目标 20 → 1 天 → 明天（9 月 9 日）；单数用 day。
    expect(formatFinishEstimate(estimate, TODAY)).toBe(
      'Estimated 1 day to finish at 20 words/day (by Sep 9)'
    );
  });

  it('only shows the day count when days > 30', () => {
    const estimate = estimateFinishDays(1000, 0, [], 20, TODAY);
    expect(formatFinishEstimate(estimate, TODAY)).toBe(
      'Estimated 50 days to finish at 20 words/day'
    );
  });

  it('appends the year when the finish date crosses into the next year', () => {
    const estimate = estimateFinishDays(40, 0, [], 20, new Date(2026, 11, 30));
    // 2026-12-30 + 2 天 → 2027-01-01，跨年带年份。
    expect(formatFinishEstimate(estimate, new Date(2026, 11, 30))).toBe(
      'Estimated 2 days to finish at 20 words/day (by Jan 1, 2027)'
    );
  });

  it('renders the English completion copy when nothing remains', () => {
    expect(formatFinishEstimate({ kind: 'done' }, TODAY)).toBe(
      'All new words learned — keep reviewing.'
    );
  });

  it('renders nothing when the estimate is unavailable', () => {
    expect(formatFinishEstimate({ kind: 'unavailable' }, TODAY)).toBe('');
  });
});

describe('finishEstimateParts', () => {
  it('exposes the day count separately so TodayView can wrap it in a code pill', () => {
    const estimate = estimateFinishDays(220, 20, [], 20, TODAY);
    const parts = finishEstimateParts(estimate, TODAY);
    expect(parts).toMatchObject({
      kind: 'estimate',
      lead: 'Estimated ',
      days: 10,
      mid: ' days to finish at ',
      speed: 20,
      tail: ' words/day'
    });
    if (parts.kind === 'estimate') {
      expect(parts.dateSuffix).toMatch(/^ \(by \w{3} \d{1,2}\)$/);
    }
  });
});
