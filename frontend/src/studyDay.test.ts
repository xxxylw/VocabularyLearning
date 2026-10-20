import { describe, expect, it } from 'vitest';
import type { StudyCard } from './api';
import { buildCheckInRecord, localDateString, type CheckInRecord } from './checkins';
import { currentStudyDayAnchor, studyDayKey } from './studyDay';
import { estimateFinishDays, recentDailyNewWordSpeed } from './estimate';

// 2026-09 规格「每日学习刷新时间改为凌晨 2:00」—— 前端学习日锚点。
// 学习日 = 北京时间（固定 UTC+8）自然日，边界凌晨 02:00：
// [D 02:00, D+1 02:00) 属于学习日 D。协调员拍板 D1：固定 Asia/Shanghai，
// 不跟随浏览器本地时区。全部用例以显式时区偏移构造时刻，断言与
// 测试运行环境的本地时区无关。

describe('studyDayKey / currentStudyDayAnchor', () => {
  // 规格第六章边界用例：01:59:59 → 旧学习日；02:00:00.000 / 02:00:01 → 新学习日。
  it('maps 01:59:59 Beijing to the previous study day', () => {
    // 北京 2026-09-12 01:59:59 = UTC 2026-09-11 17:59:59
    expect(studyDayKey(new Date('2026-09-11T17:59:59.000Z'))).toBe('2026-09-11');
  });

  it('maps 02:00:00.000 Beijing (the boundary itself) to the new study day', () => {
    expect(studyDayKey(new Date('2026-09-11T18:00:00.000Z'))).toBe('2026-09-12');
  });

  it('maps 02:00:01 Beijing to the new study day', () => {
    expect(studyDayKey(new Date('2026-09-11T18:00:01.000Z'))).toBe('2026-09-12');
  });

  it('keeps the natural day after the boundary (18:30 Beijing)', () => {
    expect(studyDayKey(new Date('2026-09-11T10:30:00.000Z'))).toBe('2026-09-11');
  });

  it('rolls across month boundaries (Beijing Oct 1 01:30 → Sep 30)', () => {
    expect(studyDayKey(new Date('2026-09-30T17:30:00.000Z'))).toBe('2026-09-30');
    expect(studyDayKey(new Date('2026-10-01T01:30:00+08:00'))).toBe('2026-09-30');
  });

  it('rolls across year boundaries (Beijing Jan 1 00:30 → Dec 31)', () => {
    expect(studyDayKey(new Date('2026-01-01T00:30:00+08:00'))).toBe('2025-12-31');
  });

  it('ignores the instant\'s originating timezone label (only the UTC instant matters)', () => {
    expect(studyDayKey(new Date('2026-09-11T10:00:00+08:00'))).toBe('2026-09-11');
    expect(studyDayKey(new Date('2026-09-11T02:00:00+00:00'))).toBe('2026-09-11');
  });

  // 固定北京时区，而非运行环境本地/UTC 时区：UTC 时刻 2026-09-11 18:30
  // 在 UTC/本地口径下都是 09-11，但学习日已是 09-12。
  it('uses fixed +08:00, not the runner\'s local timezone', () => {
    const instant = new Date('2026-09-11T18:30:00.000Z');
    expect(studyDayKey(instant)).toBe('2026-09-12');
  });

  it('anchors a local-midnight Date whose calendar fields equal the study day', () => {
    const anchor = currentStudyDayAnchor(new Date('2026-09-11T18:30:00.000Z'));
    expect(anchor.getFullYear()).toBe(2026);
    expect(anchor.getMonth()).toBe(8);
    expect(anchor.getDate()).toBe(12);
    // 锚点与日期键互为印证 —— 下游 localDateString(anchor) 即学习日键。
    expect(localDateString(anchor)).toBe('2026-09-12');
  });
});

describe('buildCheckInRecord follows the study-day boundary', () => {
  it('dates a 00:30 Beijing completion to the previous natural day (spec ch.6)', () => {
    const record = buildCheckInRecord([] as StudyCard[], new Date('2026-09-11T16:30:00.000Z'));
    expect(record.date).toBe('2026-09-11');
  });

  it('dates a 02:10 Beijing completion to the new natural day', () => {
    const record = buildCheckInRecord([] as StudyCard[], new Date('2026-09-11T18:10:00.000Z'));
    expect(record.date).toBe('2026-09-12');
  });

  it('dates an evening completion to the same natural day', () => {
    const record = buildCheckInRecord([] as StudyCard[], new Date('2026-09-11T10:30:00.000Z'));
    expect(record.date).toBe('2026-09-11');
  });
});

describe('finish estimate window follows the study day', () => {
  function checkInOn(day: string, newCards: number): CheckInRecord {
    return {
      date: day,
      completedCards: newCards + 2,
      newCards,
      reviewCards: 2,
      completedAt: `${day}T21:00:00.000Z`
    };
  }

  // 规格第六章：北京 00:30 查询背完预估 —— 学习日内（含前一晚深夜）
  // 的打卡样本仍落在 14 天窗口内，速度取中位数，不回退默认目标。
  it('keeps the study-day samples inside the 14-day window at 00:30 Beijing (no target fallback)', () => {
    // 学习日 2026-09-11：样本 09-09 / 09-10 / 09-11。
    const checkIns = [
      checkInOn('2026-09-08', 30),
      checkInOn('2026-09-09', 10),
      checkInOn('2026-09-10', 20),
      checkInOn('2026-09-11', 30)
    ];
    // 北京 2026-09-12 00:30（仍属学习日 09-11）。
    const anchor = currentStudyDayAnchor(new Date('2026-09-11T16:30:00.000Z'));

    const speed = recentDailyNewWordSpeed(checkIns, anchor, 20);
    expect(speed.source).toBe('median');
    expect(speed.speed).toBe(25);

    const estimate = estimateFinishDays(120, 0, checkIns, 20, anchor);
    expect(estimate.kind === 'estimate' ? estimate.speedSource : '').toBe('median');
  });

  it('drops samples from before the study-day window', () => {
    // 窗口 = [学习日 − 13, 学习日]；学习日 09-11 → 起点 08-29。
    const checkIns = [
      checkInOn('2026-08-27', 30),
      checkInOn('2026-08-28', 40),
      checkInOn('2026-08-29', 10)
    ];
    const anchor = currentStudyDayAnchor(new Date('2026-09-11T10:30:00.000Z'));

    expect(recentDailyNewWordSpeed(checkIns, anchor, 20)).toEqual({
      speed: 20,
      source: 'target'
    });
  });
});
