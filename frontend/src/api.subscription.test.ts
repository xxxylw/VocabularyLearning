import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cancelSubscription,
  createMockOrder,
  fetchSubscriptionMe,
  fetchSubscriptionPlan,
  formatPrice
} from './api';
import type { SubscriptionPlan } from './api';
import { setSessionToken } from './session';

// C-09/C-10 subscription API contract tests: the four endpoints ride
// the authJson helper (token header + structured ApiError), and
// formatPrice() is the single place price digits are derived — the
// subscription UI must never hardcode an amount.

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const plan: SubscriptionPlan = {
  plan: 'monthly',
  priceCents: 10,
  currency: 'CNY',
  period: 'month'
};

describe('subscription api', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('fetches the plan without a token header when anonymous', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(plan));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchSubscriptionPlan();

    expect(result.priceCents).toBe(10);
    expect(fetchMock).toHaveBeenCalledWith('/api/subscription/plan', {
      method: 'GET',
      headers: {}
    });
  });

  it('attaches the Authorization header for the authenticated endpoints', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(plan))
      .mockResolvedValueOnce(
        jsonResponse({ subscribed: false, plan: null, status: null, startedAt: null, expiresAt: null, autoRenew: null, source: null })
      )
      .mockResolvedValueOnce(
        jsonResponse({ subscribed: true, plan: 'monthly', status: 'active', startedAt: '2026-09-05T00:00:00+00:00', expiresAt: '2026-10-05T00:00:00+00:00', autoRenew: true, source: 'mock' })
      )
      .mockResolvedValueOnce(
        jsonResponse({ subscribed: false, plan: 'monthly', status: 'canceled', startedAt: '2026-09-05T00:00:00+00:00', expiresAt: '2026-10-05T00:00:00+00:00', autoRenew: false, source: 'mock' })
      );
    vi.stubGlobal('fetch', fetchMock);

    await fetchSubscriptionPlan();
    await fetchSubscriptionMe();
    const ordered = await createMockOrder();
    expect(ordered.subscribed).toBe(true);
    const canceled = await cancelSubscription();
    expect(canceled.subscribed).toBe(false);

    for (const call of fetchMock.mock.calls) {
      expect(call[1].headers.Authorization).toBe('Bearer stored-token');
    }
  });

  it('sends mock-order and cancel as POSTs without a body', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ subscribed: true, plan: 'monthly', status: 'active', startedAt: null, expiresAt: null, autoRenew: true, source: 'mock' })
    );
    vi.stubGlobal('fetch', fetchMock);

    await createMockOrder();
    await cancelSubscription();

    expect(fetchMock.mock.calls[0][0]).toBe('/api/subscription/mock-order');
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
    expect(fetchMock.mock.calls[1][0]).toBe('/api/subscription/cancel');
    expect(fetchMock.mock.calls[1][1].method).toBe('POST');
  });

  it('surfaces 409 super conflicts as structured ApiErrors', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: { code: 'super_account', message: 'super 账号无需订阅' } },
        409
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await createMockOrder().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect((error as { status: number }).status).toBe(409);
    expect((error as { code?: string }).code).toBe('super_account');
  });
});

describe('formatPrice', () => {
  it('splits the mock 0.1 yuan plan into ¥ 0 + .1 + / 月', () => {
    const parts = formatPrice(plan);
    expect(parts.currencySymbol).toBe('¥');
    expect(parts.integer).toBe('0');
    expect(parts.fraction).toBe('.1');
    expect(parts.periodLabel).toBe('/ 月');
  });

  it('renders a 4.99 config change with zero structural drift', () => {
    const parts = formatPrice({ ...plan, priceCents: 499 });
    expect(parts.integer).toBe('4');
    expect(parts.fraction).toBe('.99');
    expect(parts.currencySymbol).toBe('¥');
    expect(parts.periodLabel).toBe('/ 月');
  });

  it('pads sub-10-cent fractions and drops whole amounts', () => {
    expect(formatPrice({ ...plan, priceCents: 5 }).fraction).toBe('.05');
    expect(formatPrice({ ...plan, priceCents: 100 }).fraction).toBe('');
    expect(formatPrice({ ...plan, priceCents: 100 }).integer).toBe('1');
  });

  it('falls back to the raw currency code and period for unknown values', () => {
    const parts = formatPrice({ plan: 'p', priceCents: 10, currency: 'GBP', period: 'quarter' });
    expect(parts.currencySymbol).toBe('GBP');
    expect(parts.periodLabel).toBe('/ quarter');
  });
});
