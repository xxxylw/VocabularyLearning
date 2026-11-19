import type { StudyCard } from './api';
import { studyDayKey } from './studyDay';

export type CheckInRecord = {
  date: string;
  completedCards: number;
  newCards: number;
  reviewCards: number;
  completedAt: string;
  // 2026-11-18 已背计数口径：cardId -> queueType 映射。同日多次完成
  // （再来一组 / 重复池重刷 / ad-hoc 复习）按 cardId 并集去重计数，
  // 同一张卡只计 1 次，已背数永远 ≤ 当天队列卡数。旧记录没有该
  // 字段时，同日合并回退 Math.max（不再累加）。
  cardTypes?: Record<string, StudyCard['queueType']>;
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
  // 2026-11-18 已背计数口径：同一 cardId 因队列快照重复行 / 重复池
  // 重现出现多次时只计 1 次 —— 先折叠成 cardId -> queueType 映射，
  // 计数取唯一 cardId 数（后出现的 queueType 覆盖前行）。
  const cardTypes: Record<string, StudyCard['queueType']> = {};
  for (const card of cards) {
    cardTypes[card.cardId] = card.queueType;
  }
  const uniqueCardIds = Object.keys(cardTypes);
  return {
    // 2026-09 学习日边界 02:00：本地回退记录按学习日归日（北京
    // 00:00–02:00 完成记到前一自然日），与服务端由 reviews.study_date
    // 派生的打卡口径一致；completedAt 时间戳仍存提交时刻。
    date: studyDayKey(completedAt),
    completedCards: uniqueCardIds.length,
    newCards: uniqueCardIds.filter((cardId) => cardTypes[cardId] === 'new').length,
    reviewCards: uniqueCardIds.filter((cardId) => cardTypes[cardId] === 'review').length,
    completedAt: completedAt.toISOString(),
    cardTypes
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

  // 2026-11-18 已背计数口径：同日多次完成（再来一组 / 重复池重刷 /
  // ad-hoc 复习）按 cardId 并集去重计数 —— 同一张卡重复完成只计
  // 1 次，已背数永远 ≤ 当天队列卡数；旧记录（无 cardTypes）回退
  // Math.max（不再累加），避免重刷把已背数顶过队列上限。
  let nextRecords: CheckInRecord[];
  if (!existing) {
    nextRecords = [...records, record];
  } else {
    const cardTypes =
      existing.cardTypes && record.cardTypes
        ? { ...existing.cardTypes, ...record.cardTypes }
        : null;
    const uniqueCardIds = cardTypes ? Object.keys(cardTypes) : [];
    nextRecords = records.map((item) =>
      item.date === record.date
        ? {
            ...item,
            completedCards: cardTypes
              ? uniqueCardIds.length
              : Math.max(item.completedCards, record.completedCards),
            newCards: cardTypes
              ? uniqueCardIds.filter((cardId) => cardTypes[cardId] === 'new').length
              : Math.max(item.newCards, record.newCards),
            reviewCards: cardTypes
              ? uniqueCardIds.filter((cardId) => cardTypes[cardId] === 'review').length
              : Math.max(item.reviewCards, record.reviewCards),
            completedAt: record.completedAt,
            cardTypes: cardTypes ?? item.cardTypes
          }
        : item
    );
  }

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
