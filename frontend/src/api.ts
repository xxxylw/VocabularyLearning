export type StudyCard = {
  cardId: string;
  word: string;
  partOfSpeech: string;
  senseLabel: string;
  definition: string;
  examples: Array<{ exampleId: string; sentence: string; isPrimary: boolean }>;
  chineseNote: string | null;
  queueType: 'new' | 'review';
};

export type ReviewRating = 'known' | 'uncertain' | 'unknown';

export type TodaySession = {
  totalCards: number;
  cards: StudyCard[];
};

export type ExportResult =
  | { status: 'ready'; downloadUrl: string; cardCount: number }
  | { status: 'missing'; totalWords: number; preparedWords: number; missingWords: number };

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (!response.ok && response.status !== 409) {
    const errorBody = await readResponseBody(response);
    const statusText = response.statusText ? ` ${response.statusText}` : '';
    const detail = errorBody ? `: ${errorBody}` : '';

    throw new Error(`POST ${url} failed with ${response.status}${statusText}${detail}`);
  }

  const bodyText = await response.text();

  if (!bodyText) {
    return undefined as T;
  }

  try {
    return JSON.parse(bodyText) as T;
  } catch {
    throw new Error(`POST ${url} returned an invalid JSON response`);
  }
}

async function readResponseBody(response: Response): Promise<string> {
  const bodyText = await response.text().catch(() => '');

  if (!bodyText) {
    return '';
  }

  try {
    const parsed = JSON.parse(bodyText) as unknown;

    if (typeof parsed === 'string') {
      return parsed;
    }

    if (isErrorObject(parsed)) {
      return parsed.message ?? parsed.error ?? '';
    }
  } catch {
    return bodyText;
  }

  return bodyText;
}

function isErrorObject(value: unknown): value is { message?: string; error?: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    (typeof (value as { message?: unknown }).message === 'string' ||
      typeof (value as { error?: unknown }).error === 'string')
  );
}

function localDateString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function startTodaySession(): Promise<TodaySession> {
  return postJson<TodaySession>('/api/study/today/start', {
    dailyNewWordTarget: 20
  });
}

export function reviewCard(cardId: string, rating: ReviewRating): Promise<unknown> {
  const reviewedAt = new Date();

  return postJson(`/api/cards/${cardId}/reviews`, {
    rating,
    reviewedAt: reviewedAt.toISOString(),
    reviewedDate: localDateString(reviewedAt)
  });
}

export async function exportFullBook(): Promise<ExportResult> {
  const data = await postJson<
    | { downloadUrl: string; cardCount: number }
    | { totalWords: number; preparedWords: number; missingWords: number }
  >('/api/export/anki/full-book', {
    deckName: 'Vocabulary Learning Full Book',
    includeChineseNote: true
  });

  if ('downloadUrl' in data) {
    return { status: 'ready', downloadUrl: data.downloadUrl, cardCount: data.cardCount };
  }

  return {
    status: 'missing',
    totalWords: data.totalWords,
    preparedWords: data.preparedWords,
    missingWords: data.missingWords
  };
}
