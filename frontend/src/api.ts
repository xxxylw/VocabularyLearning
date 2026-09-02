export type DefinitionSource =
  | 'manual'
  | 'oxford_api'
  | 'open_api'
  | 'imported'
  | 'ai'
  | 'experimental_html'
  | 'fallback';

export type StudyCard = {
  cardId: string;
  cardIds: string[];
  word: string;
  partOfSpeech: string;
  senseLabel: string;
  definition: string;
  definitionSource: DefinitionSource;
  examples: Array<{ exampleId: string; sentence: string; isPrimary: boolean }>;
  chineseNote: string | null;
  senses: Array<{
    cardId: string;
    partOfSpeech: string;
    senseLabel: string;
    definition: string;
    definitionSource: DefinitionSource;
    examples: Array<{ exampleId: string; sentence: string; isPrimary: boolean }>;
    chineseNote: string | null;
  }>;
  queueType: 'new' | 'review';
  degraded: boolean;
};

export type ReviewRating = 'known' | 'uncertain' | 'unknown';

export type TodaySession = {
  totalCards: number;
  cards: StudyCard[];
};

export type BookProgress = {
  totalWords: number;
  nextSequenceIndex: number | null;
};

export type BookInfo = {
  id: string;
  title: string;
  description: string | null;
  source: string | null;
  createdAt: string;
  updatedAt: string;
  totalWords: number;
};

export type OxfordLookupResult = {
  word: string;
  sourceUrl: string;
  senses: Array<{
    partOfSpeech: string;
    definition: string;
    example: string | null;
  }>;
};

export type Pronunciation = {
  word: string;
  ipa: string | null;
  ipaUk?: string | null;
  ipaUs?: string | null;
  audioUrl: string | null;
  sourceUrl: string;
  audioSourceUrl: string | null;
  attribution: string | null;
  license: string | null;
  licenseUrl: string | null;
  status: 'ready' | 'unavailable';
};

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
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

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    const errorBody = await readResponseBody(response);
    const statusText = response.statusText ? ` ${response.statusText}` : '';
    const detail = errorBody ? `: ${errorBody}` : '';

    throw new Error(`GET ${url} failed with ${response.status}${statusText}${detail}`);
  }

  const bodyText = await response.text();

  if (!bodyText) {
    return undefined as T;
  }

  try {
    return JSON.parse(bodyText) as T;
  } catch {
    throw new Error(`GET ${url} returned an invalid JSON response`);
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

export function startTodaySession(dailyNewWordTarget = 20): Promise<TodaySession> {
  return postJson<TodaySession>('/api/study/today/start', {
    dailyNewWordTarget
  });
}

export function getBookProgress(): Promise<BookProgress> {
  return getJson<BookProgress>('/api/book-words/progress');
}

export function getCurrentBook(): Promise<BookInfo> {
  return getJson<BookInfo>('/api/books/current');
}

export function lookupOxfordWord(word: string): Promise<OxfordLookupResult> {
  return getJson<OxfordLookupResult>(`/api/lookup/oxford?word=${encodeURIComponent(word)}`);
}

export function lookupPronunciation(word: string): Promise<Pronunciation> {
  return getJson<Pronunciation>(`/api/pronunciations/${encodeURIComponent(word)}`);
}

export function reviewCard(cardId: string, rating: ReviewRating): Promise<unknown> {
  const reviewedAt = new Date();

  return postJson(`/api/cards/${cardId}/reviews`, {
    rating,
    reviewedAt: reviewedAt.toISOString(),
    reviewedDate: localDateString(reviewedAt)
  });
}