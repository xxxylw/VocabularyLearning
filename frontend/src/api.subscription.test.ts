import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cancelOrder,
  createOrder,
  fetchLatestOrder,
  fetchSubscriptionMe,
  fetchSubscriptionPlans,
  formatPrice,
  setRenewReminder
} from './api';
import { setSessionToken } from './session';

// v3 subscription + payment API contract tests (V3-01/02/03): the
// endpoints ride the authJson helper (token header + structured
// ApiError), and formatPrice() stays the single place price digits are
// derived — the subscription UI must never hardcode an amount. The v2
// mock-order / cancel wrappers are retired (V3-08) and must not come
// back.

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const plansPayload = {
  plans: [
    { plan: 'monthly', label: '单月', priceCents: 500, currency: 'CNY', durationDays: 30 },
    { plan: 'renew', label: '续费优惠', priceCents: 299, currency: 'CNY', durationDays: 30 },
    { plan: 'halfyear', label: '半年卡', priceCents: 1700, currency: 'CNY', durationDays: 180 },
    { plan: 'yearly', label: '年卡', priceCents: 3400, currency: 'CNY', durationDays: 360 }
  ],
  currency: 'CNY',
  trialDays: 7,
  renewGraceDays: 7,
  renewEligible: false,
  paymentEnabled: true,
  channels: { wechat: true, alipay: true }
};

const statusPayload = {
  subscribed: false,
  plan: null,
  status: null,
  startedAt: null,
  expiresAt: null,
  autoRenew: null,
  source: null,
  trialDaysLeft: null,
  readOnly: false,
  renewEligible: false,
  renewDeadline: null,
  renewReminder: null
};

const orderPayload = {
  outTradeNo: 'VL202609061234567890abcdef',
  plan: 'monthly',
  amountCents: 500,
  currency: 'CNY',
  status: 'pending',
  channel: 'wechat',
  payUrl: null,
  payQrUrl: 'weixin://wxpay/bizpayurl?pr=abc123',
  createdAt: '2026-09-06T12:00:00+00:00',
  paidAt: null,
  expiresAt: '2026-09-06T12:15:00+00:00'
};

describe('subscription api', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('fetches the plans with a token header when authenticated', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(plansPayload));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchSubscriptionPlans();

    expect(result.plans).toHaveLength(4);
    expect(result.paymentEnabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith('/api/subscription/plans', {
      method: 'GET',
      headers: { Authorization: 'Bearer stored-token' }
    });
  });

  it('sends createOrder as a POST with the plan + channel body', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(orderPayload));
    vi.stubGlobal('fetch', fetchMock);

    const order = await createOrder('monthly', 'wechat');

    expect(order.outTradeNo).toBe(orderPayload.outTradeNo);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/subscription/orders');
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ plan: 'monthly', channel: 'wechat' });
  });

  it('polls the latest order and cancels by out_trade_no', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ order: orderPayload, subscription: { ...statusPayload, subscribed: false } })
      )
      .mockResolvedValueOnce(jsonResponse({ ...orderPayload, status: 'closed' }));
    vi.stubGlobal('fetch', fetchMock);

    const latest = await fetchLatestOrder();
    expect(latest.order?.status).toBe('pending');

    await cancelOrder(orderPayload.outTradeNo);
    expect(fetchMock.mock.calls[1][0]).toBe(
      `/api/subscription/orders/${orderPayload.outTradeNo}/cancel`
    );
    expect(fetchMock.mock.calls[1][1].method).toBe('POST');
  });

  it('updates the renew reminder via PUT', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...statusPayload, renewReminder: false })
    );
    vi.stubGlobal('fetch', fetchMock);

    const status = await setRenewReminder(false);

    expect(status.renewReminder).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/subscription/reminder');
    expect(fetchMock.mock.calls[0][1].method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ enabled: false });
  });

  it('surfaces 503 payment_not_configured as a structured ApiError', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: { code: 'payment_not_configured', message: '支付通道尚未开通' } },
        503
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await createOrder('monthly', 'alipay').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect((error as { status: number }).status).toBe(503);
    expect((error as { code?: string }).code).toBe('payment_not_configured');
  });

  it('still answers the v2 status read with the v3 fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...statusPayload, status: 'trialing', trialDaysLeft: 7, subscribed: true })
    );
    vi.stubGlobal('fetch', fetchMock);

    const status = await fetchSubscriptionMe();

    expect(status.trialDaysLeft).toBe(7);
    expect(status.readOnly).toBe(false);
  });
});

describe('formatPrice', () => {
  it('splits the 5 yuan monthly tier into ¥ 5 + / 月', () => {
    const parts = formatPrice(500, 'CNY', 30);
    expect(parts.currencySymbol).toBe('¥');
    expect(parts.integer).toBe('5');
    expect(parts.fraction).toBe('');
    expect(parts.periodLabel).toBe('/ 月');
  });

  it('renders the 2.99 renew tier with a padded fraction', () => {
    const parts = formatPrice(299, 'CNY', 30);
    expect(parts.integer).toBe('2');
    expect(parts.fraction).toBe('.99');
    expect(parts.periodLabel).toBe('/ 月');
  });

  it('labels the halfyear and yearly tiers by duration', () => {
    expect(formatPrice(1700, 'CNY', 180).periodLabel).toBe('/ 半年');
    expect(formatPrice(3400, 'CNY', 360).periodLabel).toBe('/ 1 年');
  });

  it('falls back to the raw currency code for unknown values', () => {
    const parts = formatPrice(10, 'GBP', 30);
    expect(parts.currencySymbol).toBe('GBP');
  });
});
