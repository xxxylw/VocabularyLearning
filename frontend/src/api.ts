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

// P0 2026-09-08 跨设备完成态恢复：只读 summary，用作 Today 页
// 「Start today cards / 再来一组 + 练习拼写」按钮组切换的判定源。
// 后端不挂 study-entitlement gate — 即便到期锁定也能读到。
export type TodaySummary = {
  studyDate: string;
  totalCards: number;
  reviewedCards: number;
  dayCompleted: boolean;
  completedCards: StudyCard[];
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
    const { text: errorBody, code } = await readErrorInfo(response);
    const statusText = response.statusText ? ` ${response.statusText}` : '';
    const detail = errorBody ? `: ${errorBody}` : '';

    // QA P2 (frontend half): v1.1 threw a plain string Error, so views
    // could not branch on status/code (e.g. 403 subscription_expired).
    // Same message text, now structured.
    throw new ApiError(
      response.status,
      `${method} ${url} failed with ${response.status}${statusText}${detail}`,
      code
    );
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
    const { text: errorBody, code } = await readErrorInfo(response);
    const statusText = response.statusText ? ` ${response.statusText}` : '';
    const detail = errorBody ? `: ${errorBody}` : '';

    // QA P2: structured error (status + code), v1.1 message preserved.
    throw new ApiError(
      response.status,
      `GET ${url} failed with ${response.status}${statusText}${detail}`,
      code
    );
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

async function readErrorInfo(response: Response): Promise<{ text: string; code?: string }> {
  // QA P2: single pass over the error body — `text` keeps the exact
  // v1.1 readResponseBody semantics (api.test.ts asserts the resulting
  // message strings), `code` additionally surfaces FastAPI's
  // detail.code (e.g. subscription_expired) for branch-on-error views.
  const bodyText = await response.text().catch(() => '');

  if (!bodyText) {
    return { text: '' };
  }

  try {
    const parsed = JSON.parse(bodyText) as unknown;

    if (typeof parsed === 'string') {
      return { text: parsed };
    }

    if (isErrorObject(parsed)) {
      const code = (parsed as { code?: unknown }).code;
      return {
        text: parsed.message ?? parsed.error ?? '',
        code: typeof code === 'string' ? code : undefined
      };
    }

    const detail = (parsed as { detail?: unknown } | null)?.detail;
    if (detail !== null && typeof detail === 'object' && detail !== undefined) {
      const code = (detail as { code?: unknown }).code;
      if (typeof code === 'string') {
        return { text: bodyText, code };
      }
    }
  } catch {
    return { text: bodyText };
  }

  return { text: bodyText };
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

export function startTodaySession(
  dailyNewWordTarget = 20,
  extraNewWords = 0
): Promise<TodaySession> {
  // P0 2026-09-08 「再来一组」：extraNewWords 是单次追加 delta
  // （不与日常配额合并计算；只在该次调用的 merge 路径生效，
  // 不落库，跨日不残留）。详见 backend/app/services.py
  // _merge_new_cards_into_today_queue 的注释。
  return postJson<TodaySession>('/api/study/today/start', {
    dailyNewWordTarget,
    extraNewWords
  });
}

export function fetchTodaySummary(date?: string): Promise<TodaySummary> {
  const query = date ? `?date=${encodeURIComponent(date)}` : '';
  return getJson<TodaySummary>(`/api/study/today/summary${query}`);
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

async function authJson<T>(method: 'GET' | 'POST' | 'PUT', url: string, body?: unknown): Promise<T> {
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

export function verifyEmailCode(email: string, code: string): Promise<TokenEmail> {
  // C-01a: email activation moved from 1-hour links to a 6-digit code
  // typed into the check-email page. The legacy GET entry point answers
  // 410 link_disabled and is intentionally NOT wrapped here.
  return authJson<TokenEmail>('POST', '/api/auth/verify-email', { email, code });
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

export function resetPassword(email: string, code: string, newPassword: string): Promise<void> {
  // C-01a: the reset flow uses the same 6-digit code semantics as
  // activation — {email, code, newPassword} — instead of a link token.
  return authJson<void>('POST', '/api/auth/reset-password', { email, code, newPassword });
}

// ---------------------------------------------------------------------------
// v3 subscription + payment API (v3 P0, V3-01/02/03). Same authJson helper
// as the auth endpoints so failures surface as structured ApiErrors. All
// prices come from the backend plans payload (config-driven: 改价不发版)
// — the UI renders them through formatPrice() and never hardcodes an
// amount into copy. The v2 mock-order / cancel endpoints are retired for
// normal users (V3-08) and are NOT wrapped here anymore.
// ---------------------------------------------------------------------------

export type SubscriptionStatus = {
  subscribed: boolean;
  plan: string | null;
  status: string | null;
  startedAt: string | null;
  expiresAt: string | null;
  autoRenew: boolean | null;
  source: string | null;
  // v3 extensions (V3-01/V3-02): trial countdown, read-only flag,
  // renew-eligibility snapshot (server-judged) and the 续费提醒开关.
  trialDaysLeft: number | null;
  readOnly: boolean;
  renewEligible: boolean;
  renewDeadline: string | null;
  renewReminder: boolean | null;
};

export type SubscriptionTier = {
  plan: string;
  label: string;
  priceCents: number;
  currency: string;
  durationDays: number;
};

export type PaymentChannel = 'wechat' | 'alipay';

export type SubscriptionPlans = {
  plans: SubscriptionTier[];
  currency: string;
  trialDays: number;
  renewGraceDays: number;
  renewEligible: boolean;
  paymentEnabled: boolean;
  // 官方双通道逐渠道可用性（密钥未配置的渠道为 false，收银台置灰）。
  channels: Record<PaymentChannel, boolean>;
};

export type PaymentOrder = {
  outTradeNo: string;
  plan: string;
  amountCents: number;
  currency: string;
  status: string;
  channel: string;
  payUrl: string | null;
  payQrUrl: string | null;
  createdAt: string;
  paidAt: string | null;
  expiresAt: string | null;
};

export type LatestOrder = {
  order: PaymentOrder | null;
  subscription: SubscriptionStatus;
};

export function fetchSubscriptionPlans(): Promise<SubscriptionPlans> {
  return authJson<SubscriptionPlans>('GET', '/api/subscription/plans');
}

export function fetchSubscriptionMe(): Promise<SubscriptionStatus> {
  return authJson<SubscriptionStatus>('GET', '/api/subscription/me');
}

// V3-03 下单: the backend snapshots the payable amount from the user's
// subscription state (续费窗口内 2.99 / 逾期标价 — 后端判定) and creates
// a pending order. Channel is explicit: wechat → Native code_url (扫码),
// alipay → page/wap pay 跳转官方收银台.
export function createOrder(plan: string, channel: PaymentChannel): Promise<PaymentOrder> {
  return authJson<PaymentOrder>('POST', '/api/subscription/orders', { plan, channel });
}

// 收银台轮询 + 补单兜底: also answers with the fresh subscription view
// so the checkout can flip to the status card the moment the gateway
// confirms (回调可能先于用户刷新).
export function fetchLatestOrder(): Promise<LatestOrder> {
  return authJson<LatestOrder>('GET', '/api/subscription/orders/latest');
}

// 订单级「取消支付」(V3-02 附则: 收银台 15 分钟倒计时超时自动关单,
// 用户也可主动取消 — 这是全站唯二合法的「取消」字样之一).
export function cancelOrder(outTradeNo: string): Promise<PaymentOrder> {
  return authJson<PaymentOrder>(
    'POST',
    `/api/subscription/orders/${encodeURIComponent(outTradeNo)}/cancel`
  );
}

// 续费提醒开关 (管理页唯一用户自主开关, 默认开; 只控制 T-3/T-1 站内
// 提醒与到期邮件, 不影响任何权益).
export function setRenewReminder(enabled: boolean): Promise<SubscriptionStatus> {
  return authJson<SubscriptionStatus>('PUT', '/api/subscription/reminder', { enabled });
}

// C-10 → v3: price is data, not visuals. formatPrice turns cents +
// currency into display parts so the tier cards can typeset the integer
// portion large and the fraction small — a config price change alters
// nothing here but the digits themselves.
export type PriceParts = {
  currencySymbol: string;
  integer: string;
  fraction: string;
  periodLabel: string;
};

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: '¥',
  USD: '$',
  EUR: '€'
};

function periodLabelForDuration(durationDays: number): string {
  if (durationDays === 30) {
    return '/ 月';
  }
  if (durationDays % 360 === 0) {
    return `/ ${durationDays / 360} 年`;
  }
  if (durationDays % 180 === 0) {
    return '/ 半年';
  }
  if (durationDays % 30 === 0) {
    return `/ ${durationDays / 30} 月`;
  }
  return `/ ${durationDays} 天`;
}

export function formatPrice(
  priceCents: number,
  currency: string,
  durationDays: number
): PriceParts {
  const major = Math.floor(Math.abs(priceCents) / 100);
  const minor = Math.abs(priceCents) % 100;
  // 10 cents → ".1", 5 → ".05", 99 → ".99"; whole amounts drop the
  // fraction entirely so the baseline alignment never renders ".00".
  const minorText = minor === 0 ? '' : `.${String(minor).padStart(2, '0').replace(/0+$/, '')}`;
  return {
    currencySymbol: CURRENCY_SYMBOLS[currency.toUpperCase()] ?? currency,
    integer: String(major),
    fraction: minorText,
    periodLabel: periodLabelForDuration(durationDays)
  };
}
