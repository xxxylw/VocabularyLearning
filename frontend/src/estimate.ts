import type { CheckInRecord } from './checkins';
import { localDateString } from './checkins';

// 需求 A「背完时间预估」(2026-09-08 PM 规格 DP-A1/A2/A4/A5)：
//   预计天数 N = ceil(剩余新词数 R ÷ 每日新词速度 v)
//   - R = totalWords - learnedWords
//   - v 优先取最近 14 天内有打卡记录日期的 newCards 中位数；
//     有记录日期不足 3 天、或中位数为 0 时回退当前新词目标（默认 20）。
//   - 复习负载不计入天数（「背完」= 新词清零）。
// 全部为纯函数，纯前端实现，后端零新增接口。

export type FinishEstimate =
  | { kind: 'unavailable' }
  | { kind: 'done' }
  | {
      kind: 'estimate';
      remaining: number;
      speed: number;
      speedSource: 'median' | 'target';
      days: number;
    };

export type DailySpeed = {
  speed: number;
  source: 'median' | 'target';
};

// 最近 14 天（含今天）窗口内「有打卡记录的日期」的 newCards 中位数。
// 断档日期不补零——预估回答的是「按你的实际节奏」，补零会把断档惩罚
// 错误地折进天数（DP-A2）。
export function recentDailyNewWordSpeed(
  checkIns: CheckInRecord[],
  today: Date = new Date(),
  fallbackTarget = 20
): DailySpeed {
  const windowStart = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 13);
  const startKey = localDateString(windowStart);
  const todayKey = localDateString(today);

  const samples = checkIns
    .filter((record) => record.date >= startKey && record.date <= todayKey)
    .map((record) => record.newCards);

  if (samples.length >= 3) {
    const median = medianOf(samples);
    if (median > 0) {
      return { speed: median, source: 'median' };
    }
  }

  // 样本不足（新用户、刚换设备、清缓存）或中位数为 0（纯复习用户）
  // → 回退当前设置的新词目标。
  return { speed: Math.max(1, fallbackTarget), source: 'target' };
}

export function estimateFinishDays(
  totalWords: number,
  learnedWords: number,
  checkIns: CheckInRecord[],
  newWordTarget = 20,
  today: Date = new Date()
): FinishEstimate {
  if (!Number.isFinite(totalWords) || totalWords <= 0) {
    return { kind: 'unavailable' };
  }

  const learned = Number.isFinite(learnedWords) ? Math.max(0, learnedWords) : 0;
  const remaining = Math.max(0, totalWords - learned);

  if (remaining === 0) {
    return { kind: 'done' };
  }

  const { speed, source } = recentDailyNewWordSpeed(checkIns, today, newWordTarget);

  return {
    kind: 'estimate',
    remaining,
    speed,
    speedSource: source,
    days: Math.ceil(remaining / speed)
  };
}

// 常规文案：「按每天 X 词的节奏，预计还需 N 天背完」；
// N ≤ 30 时追加（约 M 月 D 日），> 30 只显示天数（DP-A4）；
// R = 0：「新词已学完，复习继续巩固中」。
export function formatFinishEstimate(estimate: FinishEstimate, today: Date = new Date()): string {
  if (estimate.kind === 'unavailable') {
    return '';
  }
  if (estimate.kind === 'done') {
    return '新词已学完，复习继续巩固中';
  }

  const base = `按每天 ${estimate.speed} 词的节奏，预计还需 ${estimate.days} 天背完`;

  if (estimate.days <= 30) {
    const finishDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + estimate.days);
    return `${base}（约 ${finishDate.getMonth() + 1} 月 ${finishDate.getDate()} 日）`;
  }

  return base;
}

function medianOf(values: number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 1) {
    return sorted[middle];
  }

  return (sorted[middle - 1] + sorted[middle]) / 2;
}
