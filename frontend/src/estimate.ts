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

// 2026-09-11 DP-A4 定稿（PM 规格）：预估整行英文——
// 「Estimated N days to finish at X words/day」，N ≤ 30 天时追加
// (by MMM D)（跨年带年份 (by Jan 5, 2027)），> 30 只显示天数；
// R = 0：「All new words learned — keep reviewing.」。
// 天数 N 由 TodayView 用行内 code 样式（等宽 + pill 包裹）单独渲染，
// 因此提供结构化的 finishEstimateParts；本函数是它的纯字符串拼接形态。
const MONTHS_ABBR = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
];

export type FinishEstimateParts =
  | { kind: 'unavailable' }
  | { kind: 'done'; text: string }
  | {
      kind: 'estimate';
      lead: string;
      days: number;
      mid: string;
      speed: number;
      tail: string;
      dateSuffix: string;
    };

export function finishEstimateParts(
  estimate: FinishEstimate,
  today: Date = new Date()
): FinishEstimateParts {
  if (estimate.kind === 'unavailable') {
    return { kind: 'unavailable' };
  }
  if (estimate.kind === 'done') {
    return { kind: 'done', text: 'All new words learned — keep reviewing.' };
  }

  const dayWord = estimate.days === 1 ? 'day' : 'days';
  let dateSuffix = '';

  if (estimate.days <= 30) {
    const finishDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + estimate.days);
    const sameYear = finishDate.getFullYear() === today.getFullYear();
    const dateLabel = sameYear
      ? `${MONTHS_ABBR[finishDate.getMonth()]} ${finishDate.getDate()}`
      : `${MONTHS_ABBR[finishDate.getMonth()]} ${finishDate.getDate()}, ${finishDate.getFullYear()}`;
    dateSuffix = ` (by ${dateLabel})`;
  }

  return {
    kind: 'estimate',
    lead: 'Estimated ',
    days: estimate.days,
    mid: ` ${dayWord} to finish at `,
    speed: estimate.speed,
    tail: ' words/day',
    dateSuffix
  };
}

export function formatFinishEstimate(estimate: FinishEstimate, today: Date = new Date()): string {
  const parts = finishEstimateParts(estimate, today);

  if (parts.kind === 'unavailable') {
    return '';
  }
  if (parts.kind === 'done') {
    return parts.text;
  }

  return `${parts.lead}${parts.days}${parts.mid}${parts.speed}${parts.tail}${parts.dateSuffix}`;
}

function medianOf(values: number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 1) {
    return sorted[middle];
  }

  return (sorted[middle - 1] + sorted[middle]) / 2;
}
