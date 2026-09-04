import { getSessionToken } from './session';

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
  // PRD ch.8: 1-based position in the day's queue snapshot; used so the
  // progress bar resumes at the right place after re-entering Today.
  queuePosition?: number | null;
};

export type ReviewRating = 'known' | 'uncertain' | 'unknown';

export type TodaySession = {
  totalCards: number;
  cards: StudyCard[];
  // PRD ch.8: entries of the day's queue snapshot already reviewed on the
  // study date — the numerator offset so the progress bar never restarts.
  reviewedCards: number;
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
  // PRD ch.9: per-book progress aggregates surfaced on the Today cover
  // card and bookshelf list (learned = ≥1 review, mastered = every card
  // of the word is mastered). Optional because older mock payloads omit
  // them; the UI treats a missing value as "not loaded yet".
  learnedWords?: number;
  masteredWords?: number;
  fallbackNotice?: string | null;
};

export type BookListItem = BookInfo & {
  isCurrent: boolean;
};

export type BookList = {
  books: BookListItem[];
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

// v2 cloud auth: structured error thrown by the auth endpoints so the
// views can branch on status codes (403 email_not_verified, 409
// email_taken, 410 token_invalid, 429 rate_limited, 503
// email_send_failed) instead of string-matching messages.
export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

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
  return sendJson<T>('POST', url, body);
}

async function putJson<T>(url: string, body: unknown): Promise<T> {
  return sendJson<T>('PUT', url, body);
}

async function sendJson<T>(method: 'POST' | 'PUT', url: string, body: unknown): Promise<T> {
  // v2 cloud auth: attach the session token when the user is logged in.
  // With no token the init object must stay byte-for-byte identical to
  // the v1.1 shape — api.test.ts asserts the exact fetch arguments.
  const token = getSessionToken();
  const response = await fetch(url, {
    method,
    headers: token === null
      ? { 'Content-Type': 'application/json' }
      : { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const errorBody = await readResponseBody(response);
    const statusText = response.statusText ? ` ${response.statusText}` : '';
    const detail = errorBody ? `: ${errorBody}` : '';

    throw new Error(`${method} ${url} failed with ${response.status}${statusText}${detail}`);
  }

  const bodyText = await response.text();

  if (!bodyText) {
    return undefined as T;
  }

  try {
    return JSON.parse(bodyText) as T;
  } catch {
    throw new Error(`${method} ${url} returned an invalid JSON response`);
  }
}

async function getJson<T>(url: string): Promise<T> {
  // v2 cloud auth: same conditional-token rule as sendJson — with no
  // token the call stays `fetch(url)` with no second argument.
  const token = getSessionToken();
  const response = token === null
    ? await fetch(url)
    : await fetch(url, { headers: { Authorization: `Bearer ${token}` } });

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

// PRD ch.9: bookshelf data — every book with its aggregates and the
// is_current marker on the one the study flows run against.
export function listBooks(): Promise<BookList> {
  return getJson<BookList>('/api/books');
}

// PRD ch.9: switch = pointer-only update (切换零改写); switching to the
// already-current book is an idempotent no-op on the backend.
export function switchBook(bookId: string): Promise<BookInfo> {
  return putJson<BookInfo>('/api/books/current', { bookId });
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

// ---------------------------------------------------------------------------
// v2 cloud auth API (cloud batch 1). These use their own request helper
// so failures surface as structured ApiErrors (status + code + message)
// instead of the v1.1 string messages — the auth views need to branch on
// 403 email_not_verified / 409 email_taken / 410 token_invalid /
// 429 rate_limited / 503 email_send_failed.
// ---------------------------------------------------------------------------

export type AuthUser = {
  id: string;
  email: string;
  emailVerified: boolean;
  isSuper: boolean;
};

export type LoginResult = {
  token: string;
  user: AuthUser;
};

export type RegisterResult = {
  email: string;
  message: string;
};

export type TokenEmail = {
  email: string;
};

export type EmailStatus = {
  verified: boolean;
};

function toApiError(status: number, parsed: unknown, fallbackMessage: string): ApiError {
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  if (detail !== null && typeof detail === 'object' && detail !== undefined) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof message === 'string' && message !== '') {
      return new ApiError(
        status,
        message,
        typeof code === 'string' ? code : undefined
      );
    }
  }
  if (typeof detail === 'string' && detail !== '') {
    return new ApiError(status, detail);
  }
  return new ApiError(status, fallbackMessage);
}

async function authJson<T>(method: 'GET' | 'POST', url: string, body?: unknown): Promise<T> {
  const token = getSessionToken();
  const headers: Record<string, string> = {};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (token !== null) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(
    url,
    body === undefined
      ? { method, headers }
      : { method, headers, body: JSON.stringify(body) }
  );

  const bodyText = await response.text().catch(() => '');
  let parsed: unknown = null;
  if (bodyText !== '') {
    try {
      parsed = JSON.parse(bodyText);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw toApiError(
      response.status,
      parsed,
      `请求失败（${response.status}${response.statusText ? ` ${response.statusText}` : ''}）`
    );
  }

  return parsed as T;
}

export function login(email: string, password: string): Promise<LoginResult> {
  return authJson<LoginResult>('POST', '/api/auth/login', { email, password });
}

export function register(email: string, password: string): Promise<RegisterResult> {
  return authJson<RegisterResult>('POST', '/api/auth/register', { email, password });
}

export function logout(): Promise<void> {
  return authJson<void>('POST', '/api/auth/logout');
}

export function fetchCurrentUser(): Promise<AuthUser> {
  return authJson<AuthUser>('GET', '/api/auth/me');
}

export function verifyEmail(token: string): Promise<TokenEmail> {
  return authJson<TokenEmail>(
    'GET',
    `/api/auth/verify-email?token=${encodeURIComponent(token)}`
  );
}

export function fetchEmailStatus(email: string): Promise<EmailStatus> {
  return authJson<EmailStatus>(
    'GET',
    `/api/auth/email-status?email=${encodeURIComponent(email)}`
  );
}

export function resendVerification(email: string): Promise<void> {
  return authJson<void>('POST', '/api/auth/resend-verification', { email });
}

export function requestPasswordReset(email: string): Promise<void> {
  return authJson<void>('POST', '/api/auth/forgot-password', { email });
}

export function fetchResetTokenInfo(token: string): Promise<TokenEmail> {
  return authJson<TokenEmail>(
    'GET',
    `/api/auth/reset-token-info?token=${encodeURIComponent(token)}`
  );
}

export function resetPassword(token: string, newPassword: string): Promise<void> {
  return authJson<void>('POST', '/api/auth/reset-password', { token, newPassword });
}
