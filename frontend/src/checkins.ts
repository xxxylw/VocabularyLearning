import type { StudyCard } from './api';
import { studyDayKey } from './studyDay';

export type CheckInRecord = {
  date: string;
  completedCards: number;
  newCards: number;
  reviewCards: number;
  completedAt: string;
};

const CHECK_IN_STORAGE_KEY = 'vocabulary-learning-check-ins';
// P1 2026-09-08 打卡服务端化：记录「本地历史已上报给哪个账号」。值为
// 账号标识（邮箱）；换账号登录时值不匹配 → 重新触发一次性上报。
const CHECK_IN_MERGED_FLAG_KEY = 'vocabulary-learning-check-ins-merged-for';

// 该账号的本地历史是否已上报合并过。
export function mergedCheckInsFor(
  account: string,
  storage: Storage = window.localStorage
): boolean {
  return storage.getItem(CHECK_IN_MERGED_FLAG_KEY) === account;
}

export function markCheckInsMerged(
  account: string,
  storage: Storage = window.localStorage
): void {
  storage.setItem(CHECK_IN_MERGED_FLAG_KEY, account);
}

export function localDateString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function buildCheckInRecord(cards: StudyCard[], completedAt = new Date()): CheckInRecord {
  return {
    // 2026-09 学习日边界 02:00：本地回退记录按学习日归日（北京
    // 00:00–02:00 完成记到前一自然日），与服务端由 reviews.study_date
    // 派生的打卡口径一致；completedAt 时间戳仍存提交时刻。
    date: studyDayKey(completedAt),
    completedCards: cards.length,
    newCards: cards.filter((card) => card.queueType === 'new').length,
    reviewCards: cards.filter((card) => card.queueType === 'review').length,
    completedAt: completedAt.toISOString()
  };
}

export function loadCheckIns(storage: Storage = window.localStorage): CheckInRecord[] {
  const rawValue = storage.getItem(CHECK_IN_STORAGE_KEY);

  if (!rawValue) {
    return [];
  }

  try {
    const parsed = JSON.parse(rawValue) as unknown;

    if (!Array.isArray(parsed)) {
      return [];
    }

    return parsed.filter(isCheckInRecord).sort((left, right) => left.date.localeCompare(right.date));
  } catch {
    return [];
  }
}

export function saveCheckIn(record: CheckInRecord, storage: Storage = window.localStorage): CheckInRecord[] {
  const records = loadCheckIns(storage);
  const existing = records.find((item) => item.date === record.date);

  const nextRecords = existing
    ? records.map((item) =>
        item.date === record.date
          ? {
              ...item,
              completedCards: item.completedCards + record.completedCards,
              newCards: item.newCards + record.newCards,
              reviewCards: item.reviewCards + record.reviewCards,
              completedAt: record.completedAt
            }
          : item
      )
    : [...records, record];

  nextRecords.sort((left, right) => left.date.localeCompare(right.date));
  storage.setItem(CHECK_IN_STORAGE_KEY, JSON.stringify(nextRecords));
  return nextRecords;
}

function isCheckInRecord(value: unknown): value is CheckInRecord {
  if (typeof value !== 'object' || value === null) {
    return false;
  }

  const candidate = value as Partial<CheckInRecord>;
  return (
    typeof candidate.date === 'string' &&
    typeof candidate.completedCards === 'number' &&
    typeof candidate.newCards === 'number' &&
    typeof candidate.reviewCards === 'number' &&
    typeof candidate.completedAt === 'string'
  );
}
