import { afterEach, describe, expect, it } from 'vitest';
import type { StudyCard } from './api';
import {
  buildCheckInRecord,
  saveCheckIn,
  type CheckInRecord
} from './checkins';

// 2026-11-18 前端已背计数口径（任务 7709134253405031447）：
// 首页日期格的「每日已背」= 当天队列中已清除的唯一卡片数 —— 同一
// cardId 因队列快照重复行 / 重复池重评出现多次时只计 1 次，计数
// 永远 ≤ 当天队列卡数。改的是计数口径，重复池行为本身不变。

function card(cardId: string, queueType: 'new' | 'review'): StudyCard {
  return {
    cardId,
    word: `word-${cardId}`,
    partOfSpeech: 'noun',
    senseLabel: '1',
    definition: 'definition',
    definitionSource: 'oxford',
    examples: [],
    chineseNote: null,
    queueType,
    queuePosition: 1,
    degraded: false,
    senses: []
  } as unknown as StudyCard;
}

function record(overrides: Partial<CheckInRecord>): CheckInRecord {
  return {
    date: '2026-11-18',
    completedCards: 0,
    newCards: 0,
    reviewCards: 0,
    completedAt: '2026-11-18T21:00:00.000Z',
    ...overrides
  };
}

describe('buildCheckInRecord counts unique cards (2026-11-18 cleared-count spec)', () => {
  it('counts each cardId once when the queue snapshot carries duplicate rows', () => {
    const cards = [
      card('card-1', 'new'),
      card('card-2', 'review'),
      card('card-1', 'new'),
      card('card-3', 'new'),
      card('card-2', 'review')
    ];

    const result = buildCheckInRecord(cards, new Date('2026-11-18T13:00:00.000Z'));

    expect(result.completedCards).toBe(3);
    expect(result.newCards).toBe(2);
    expect(result.reviewCards).toBe(1);
  });

  it('keeps the queueType of the last row when a cardId repeats', () => {
    const cards = [card('card-1', 'new'), card('card-1', 'review')];

    const result = buildCheckInRecord(cards, new Date('2026-11-18T13:00:00.000Z'));

    expect(result.completedCards).toBe(1);
    expect(result.newCards).toBe(0);
    expect(result.reviewCards).toBe(1);
  });

  it('records a cardId -> queueType map on cardTypes for same-day merges', () => {
    const result = buildCheckInRecord(
      [card('card-1', 'new'), card('card-2', 'review')],
      new Date('2026-11-18T13:00:00.000Z')
    );

    expect(result.cardTypes).toEqual({ 'card-1': 'new', 'card-2': 'review' });
  });

  it('returns zeroed counts for an empty completion list', () => {
    const result = buildCheckInRecord([], new Date('2026-11-18T13:00:00.000Z'));

    expect(result.completedCards).toBe(0);
    expect(result.newCards).toBe(0);
    expect(result.reviewCards).toBe(0);
    expect(result.cardTypes).toEqual({});
  });
});

describe('saveCheckIn merges same-day completions by unique cardId (cleared-count spec)', () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it('sums distinct cards across same-day sessions (One more group appends 20 new cards)', () => {
    window.localStorage.setItem(
      'vocabulary-learning-check-ins',
      JSON.stringify([
        record({
          completedCards: 40,
          newCards: 40,
          reviewCards: 0,
          cardTypes: Object.fromEntries(
            Array.from({ length: 40 }, (_, index) => [`card-${index}`, 'new'])
          )
        })
      ])
    );

    const appended = buildCheckInRecord(
      Array.from({ length: 20 }, (_, index) => card(`card-${index + 40}`, 'new')),
      new Date('2026-11-18T14:00:00.000Z')
    );
    const updated = saveCheckIn(appended);

    expect(updated).toHaveLength(1);
    expect(updated[0].completedCards).toBe(60);
    expect(updated[0].newCards).toBe(60);
    expect(updated[0].reviewCards).toBe(0);
  });

  it('does not grow the count when the same cards are completed again (repeat-pool re-entry / ad-hoc re-review)', () => {
    window.localStorage.setItem(
      'vocabulary-learning-check-ins',
      JSON.stringify([
        record({
          completedCards: 2,
          newCards: 1,
          reviewCards: 1,
          cardTypes: { 'card-1': 'new', 'card-2': 'review' }
        })
      ])
    );

    // 重复池让 card-2 再次走完评分流 —— 同一张卡只计 1 次。
    const replay = buildCheckInRecord(
      [card('card-2', 'review'), card('card-3', 'review')],
      new Date('2026-11-18T14:00:00.000Z')
    );
    const updated = saveCheckIn(replay);

    expect(updated[0].completedCards).toBe(3);
    expect(updated[0].reviewCards).toBe(2);
    // 已背数(3) ≤ 当天队列卡数（2 张原卡 + 1 张新完成卡）。
    expect(updated[0].completedCards).toBeLessThanOrEqual(3);
  });

  it('falls back to Math.max (not summation) for legacy records without cardTypes', () => {
    window.localStorage.setItem(
      'vocabulary-learning-check-ins',
      JSON.stringify([
        record({ completedCards: 25, newCards: 25, reviewCards: 0 })
      ])
    );

    const updated = saveCheckIn(
      record({
        completedCards: 10,
        newCards: 5,
        reviewCards: 5,
        cardTypes: {}
      })
    );

    expect(updated[0].completedCards).toBe(25);
    expect(updated[0].newCards).toBe(25);
    expect(updated[0].reviewCards).toBe(5);
  });

  it('keeps different study days independent', () => {
    const first = saveCheckIn(
      record({
        date: '2026-11-17',
        completedCards: 1,
        newCards: 1,
        reviewCards: 0,
        cardTypes: { 'card-1': 'new' }
      })
    );
    const second = saveCheckIn(
      record({
        completedCards: 1,
        newCards: 0,
        reviewCards: 1,
        cardTypes: { 'card-2': 'review' }
      })
    );

    expect(second).toHaveLength(2);
    expect(second[0].date).toBe('2026-11-17');
    expect(second[0].completedCards).toBe(1);
    expect(second[0].cardTypes).toEqual({ 'card-1': 'new' });
    expect(second[1].date).not.toBe('2026-11-17');
    expect(second[1].completedCards).toBe(1);
  });
});
