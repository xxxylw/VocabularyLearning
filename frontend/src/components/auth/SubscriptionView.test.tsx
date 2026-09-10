import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SubscriptionView } from './SubscriptionView';

// v3 P0 subscription page tests (V3-01/02/03): 四档价格全部来自 plans
// 载荷（配置改价 → 首屏同步变化）、状态卡三态（trialing / active /
// expired 只读）、收银台（下单 → 轮询 → 支付成功刷新）、支付未配置
// 显式提示、以及全站文案硬约束（无「自动续费 / 连续包月 / 自动扣款」、
// 无「取消订阅 / 恢复订阅」字样）。

function ok(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const TRIALING = {
  subscribed: true,
  plan: 'trial_7d',
  status: 'trialing',
  startedAt: '2026-09-01T00:00:00+00:00',
  expiresAt: '2026-09-08T00:00:00+00:00',
  autoRenew: null,
  source: 'trial',
  trialDaysLeft: 5,
  readOnly: false,
  renewEligible: false,
  renewDeadline: null,
  renewReminder: true
};

const ACTIVE = {
  subscribed: true,
  plan: 'monthly',
  status: 'active',
  startedAt: '2026-09-01T00:00:00+00:00',
  expiresAt: '2026-10-01T00:00:00+00:00',
  autoRenew: null,
  source: 'alipay',
  trialDaysLeft: null,
  readOnly: false,
  renewEligible: false,
  renewDeadline: null,
  renewReminder: true
};

const EXPIRED = {
  subscribed: false,
  plan: 'trial_7d',
  status: 'expired',
  startedAt: '2026-08-25T00:00:00+00:00',
  expiresAt: '2026-09-01T00:00:00+00:00',
  autoRenew: null,
  source: 'trial',
  trialDaysLeft: 0,
  readOnly: true,
  renewEligible: true,
  renewDeadline: '2026-09-08T00:00:00+00:00',
  renewReminder: true
};

// P2 #1（设计定稿 2026-09-07）字段口径异常态：只读徽章 + 未来 expires_at
// （v2 mock 清退遗留行的形状，走查账号线上实测），需降级为不带日期文案。
const EXPIRED_FUTURE_DATE = {
  ...EXPIRED,
  plan: 'monthly',
  source: 'mock',
  expiresAt: '2026-10-06T00:00:00+00:00',
  renewEligible: false,
  renewDeadline: null
};

// P2 #1：expires_at 不可得（空视图/清退遗留无日期）同样降级。
const EXPIRED_NO_DATE = {
  ...EXPIRED,
  expiresAt: null,
  renewEligible: false,
  renewDeadline: null
};

// 本地时区无关的期望日期（与组件 formatExpiredLine 同一口径）。
function localDatestamp(iso: string): string {
  const date = new Date(iso);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

const SUPER_VIEW = {
  subscribed: true,
  plan: null,
  status: null,
  startedAt: null,
  expiresAt: null,
  autoRenew: null,
  source: 'super',
  trialDaysLeft: null,
  readOnly: false,
  renewEligible: false,
  renewDeadline: null,
  renewReminder: null
};

const PLANS = {
  plans: [
    { plan: 'monthly', label: '单月', priceCents: 500, currency: 'CNY', durationDays: 30 },
    { plan: 'renew', label: '续费优惠', priceCents: 299, currency: 'CNY', durationDays: 30 },
    { plan: 'halfyear', label: '半年卡', priceCents: 2100, currency: 'CNY', durationDays: 180 },
    { plan: 'yearly', label: '年卡', priceCents: 3000, currency: 'CNY', durationDays: 360 }
  ],
  currency: 'CNY',
  trialDays: 7,
  renewGraceDays: 7,
  renewEligible: false,
  paymentEnabled: true,
  channels: { wechat: true, alipay: true }
};

const WECHAT_ORDER = {
  outTradeNo: 'VL20260906120000abcdef1234',
  plan: 'monthly',
  amountCents: 500,
  currency: 'CNY',
  status: 'pending',
  channel: 'wechat',
  payUrl: null,
  payQrUrl: 'weixin://wxpay/bizpayurl?pr=abc123',
  createdAt: '2026-09-06T12:00:00+00:00',
  paidAt: null,
  expiresAt: '2099-01-01T00:00:00+00:00'
};

const ALIPAY_ORDER = {
  ...WECHAT_ORDER,
  outTradeNo: 'VL20260906120000abcdef5678',
  channel: 'alipay',
  payUrl: 'https://openapi.alipay.com/gateway.do?alipay_sdk=demo',
  payQrUrl: null
};

function stubFetch(
  plansBody: unknown = PLANS,
  statusBody: unknown = TRIALING,
  extra: Record<string, unknown> = {}
) {
  return vi.fn().mockImplementation((url: string, init?: { method?: string }) => {
    if (url === '/api/subscription/plans') {
      return Promise.resolve(ok(plansBody));
    }
    if (url === '/api/subscription/me') {
      return Promise.resolve(ok(statusBody));
    }
    if (url === '/api/subscription/orders' && init?.method === 'POST') {
      return Promise.resolve(ok(extra.order ?? WECHAT_ORDER));
    }
    if (url === '/api/subscription/orders/latest') {
      return Promise.resolve(
        ok(extra.latest ?? { order: WECHAT_ORDER, subscription: statusBody })
      );
    }
    if (url.startsWith('/api/subscription/orders/') && url.endsWith('/cancel')) {
      return Promise.resolve(ok({ ...WECHAT_ORDER, status: 'closed' }));
    }
    if (url === '/api/subscription/reminder') {
      return Promise.resolve(ok({ ...(statusBody as object), renewReminder: false }));
    }
    return Promise.resolve(ok({}));
  });
}

describe('SubscriptionView', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('renders visible tiers strictly from the plans payload (renew hidden when ineligible)', async () => {
    vi.stubGlobal('fetch', stubFetch());

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-tier-monthly')).toBeInTheDocument();
    expect(screen.getByTestId('subscription-tier-halfyear')).toBeInTheDocument();
    expect(screen.getByTestId('subscription-tier-yearly')).toBeInTheDocument();
    expect(screen.getAllByText('¥')).toHaveLength(3);
    expect(screen.queryByTestId('subscription-tier-renew')).toBeNull();
    // 2026-09-10 定价调整：半年卡 21 元（原价 30，7折）、年卡 30 元
    // （原价 60，5折）——折扣标签为精确折扣，不再出现「约 5.7 折」。
    const halfyearCard = screen.getByTestId('subscription-tier-halfyear');
    expect(within(halfyearCard).getByText('7折')).toBeInTheDocument();
    expect(within(halfyearCard).getByText('21')).toBeInTheDocument();
    const yearlyCard = screen.getByTestId('subscription-tier-yearly');
    expect(within(yearlyCard).getByText('5折')).toBeInTheDocument();
    expect(within(yearlyCard).getByText('30')).toBeInTheDocument();
    expect(screen.queryByText('约 5.7 折')).toBeNull();
    // 状态卡（试用中）+ 试用剩余天数常显。
    expect(screen.getByText(/试用剩余 5 天/)).toBeInTheDocument();
  });

  it('hides the renew tier for ineligible users (server-judged)', async () => {
    vi.stubGlobal('fetch', stubFetch());

    render(<SubscriptionView />);

    await screen.findByTestId('subscription-tier-grid');
    expect(screen.queryByTestId('subscription-tier-renew')).toBeNull();
  });

  it('shows the renew tier when the backend judges the user eligible', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ ...PLANS, renewEligible: true }, EXPIRED)
    );

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-tier-renew')).toBeInTheDocument();
    expect(screen.getByTestId('subscription-tier-renew')).toHaveTextContent('2.99');
    expect(screen.getByText(/到期前或到期后 7 天内可享/)).toBeInTheDocument();
  });

  it('shows the payment-disabled banner and blocks checkout when unconfigured', async () => {
    const fetchMock = stubFetch(
      { ...PLANS, paymentEnabled: false, channels: { wechat: false, alipay: false } },
      EXPIRED
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SubscriptionView />);

    expect(await screen.findByTestId('payment-disabled')).toBeInTheDocument();
    // 档位按钮置灰：点击无效，连渠道面板都不进入。
    const tierButton = screen.getAllByRole('button', { name: '立即支付' })[0];
    expect(tierButton).toBeDisabled();
    await user.click(tierButton);
    expect(screen.queryByTestId('subscription-channel-picker')).toBeNull();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('checkout: picks wechat, creates an order, polls, and refreshes on payment success', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: { method?: string }) => {
      if (url === '/api/subscription/plans') {
        return Promise.resolve(ok(PLANS));
      }
      if (url === '/api/subscription/me') {
        return Promise.resolve(ok(EXPIRED));
      }
      if (url === '/api/subscription/orders' && init?.method === 'POST') {
        return Promise.resolve(ok(WECHAT_ORDER));
      }
      if (url === '/api/subscription/orders/latest') {
        // 第二次轮询返回已支付 + 恢复订阅。
        return Promise.resolve(
          ok({ order: { ...WECHAT_ORDER, status: 'paid' }, subscription: ACTIVE })
        );
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SubscriptionView />);

    await screen.findByTestId('subscription-tier-grid');
    await user.click(screen.getAllByRole('button', { name: '立即支付' })[0]);

    // 渠道选择 → 微信（code_url 本地渲染二维码）。
    expect(await screen.findByTestId('subscription-channel-picker')).toBeInTheDocument();
    await user.click(screen.getByTestId('channel-wechat'));
    const postCall = fetchMock.mock.calls.find(
      ([url, init]) => url === '/api/subscription/orders' && init?.method === 'POST'
    );
    expect(JSON.parse(postCall?.[1]?.body ?? '{}')).toEqual({
      plan: 'monthly',
      channel: 'wechat'
    });

    // 收银台：金额 + 本地渲染的微信二维码 + 取消支付（订单级）。
    expect(await screen.findByTestId('subscription-checkout')).toBeInTheDocument();
    expect(screen.getByText('5.00 元')).toBeInTheDocument();
    expect(await screen.findByTestId('wechat-qr')).toBeInTheDocument();
    expect(screen.getByText('取消支付')).toBeInTheDocument();

    // 轮询命中支付成功 → 状态卡刷新、收银台收起。
    await waitFor(
      () => {
        expect(screen.getByTestId('subscription-badge')).toHaveTextContent('订阅生效中');
      },
      { timeout: 5000 }
    );
    expect(screen.queryByTestId('subscription-checkout')).toBeNull();
  });

  it('checkout: alipay channel jumps to the official cashier page', async () => {
    const fetchMock = stubFetch(PLANS, EXPIRED, { order: ALIPAY_ORDER });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SubscriptionView />);

    await screen.findByTestId('subscription-tier-grid');
    await user.click(screen.getAllByRole('button', { name: '立即支付' })[0]);
    await user.click((await screen.findByTestId('channel-alipay')));
    const postCall = fetchMock.mock.calls.find(
      ([url, init]) => url === '/api/subscription/orders' && init?.method === 'POST'
    );
    expect(JSON.parse(postCall?.[1]?.body ?? '{}').channel).toBe('alipay');

    // 支付宝收银台：跳转官方收银台的链接 + 无二维码。
    expect(await screen.findByTestId('subscription-checkout')).toBeInTheDocument();
    expect(screen.getByText('支付宝支付')).toBeInTheDocument();
    expect(screen.queryByTestId('wechat-qr')).toBeNull();
    const link = screen.getByRole('link', { name: '打开支付宝收银台' });
    expect(link).toHaveAttribute('href', ALIPAY_ORDER.payUrl);
  });

  it('greys out an unconfigured single channel while the other stays usable', async () => {
    const fetchMock = stubFetch(
      { ...PLANS, channels: { wechat: true, alipay: false } },
      EXPIRED
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SubscriptionView />);

    await screen.findByTestId('subscription-tier-grid');
    await user.click(screen.getAllByRole('button', { name: '立即支付' })[0]);
    expect(await screen.findByTestId('channel-wechat')).toBeEnabled();
    expect(screen.getByTestId('channel-alipay')).toBeDisabled();
  });

  it('expired read-only state: badge + past-tense dated line + renewal CTA + skip link copy', async () => {
    vi.stubGlobal('fetch', stubFetch(PLANS, EXPIRED));

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-badge')).toHaveTextContent('已到期 · 只读模式');
    // P2 #1（设计定稿 2026-09-07）：到期主行 = 过去时「已于 X 到期」，
    // 日期与状态之间有分隔，不再出现「有效期至 X到期」无分隔拼接。
    expect(screen.getByTestId('subscription-expiry')).toHaveTextContent(
      `已于 ${localDatestamp(EXPIRED.expiresAt)} 到期 · 书架、进度与统计仍可浏览`
    );
    expect(screen.getByTestId('subscription-expiry').textContent).not.toContain('有效期至');
    expect(screen.getByRole('button', { name: '续费' })).toBeInTheDocument();
    expect(screen.getByText('返回浏览（只读模式）')).toBeInTheDocument();
  });

  it('P2 #1: anomalous future expires_at degrades to the dateless line (never a future date)', async () => {
    // 走查账号实测形状：只读徽章 + 未来 2026-10-06。钉死系统时间，
    // 保证「未来日期」判定与真实走查口径一致。
    vi.setSystemTime(new Date('2026-09-07T04:00:00+00:00'));
    vi.stubGlobal('fetch', stubFetch(PLANS, EXPIRED_FUTURE_DATE));

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-badge')).toHaveTextContent('已到期 · 只读模式');
    const line = screen.getByTestId('subscription-expiry');
    expect(line).toHaveTextContent('订阅已到期 · 书架、进度与统计仍可浏览');
    expect(line.textContent).not.toContain('2026-10-06');
    expect(line.textContent).not.toContain('有效期至');
    expect(line.textContent).not.toContain('已于');
  });

  it('P2 #1: missing expires_at renders the dateless degraded line', async () => {
    vi.stubGlobal('fetch', stubFetch(PLANS, EXPIRED_NO_DATE));

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-expiry')).toHaveTextContent(
      '订阅已到期 · 书架、进度与统计仍可浏览'
    );
    expect(screen.getByTestId('subscription-badge')).toHaveTextContent('已到期 · 只读模式');
  });

  it('active state: expiry + renew-reminder toggle wired to PUT', async () => {
    const fetchMock = stubFetch(PLANS, ACTIVE);
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-badge')).toHaveTextContent('订阅生效中');
    expect(screen.getByText(/有效期至 2026-10-01/)).toBeInTheDocument();

    // 续费提醒开关是管理侧唯一自主开关。
    const toggle = screen.getByRole('checkbox');
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/subscription/reminder' && init?.method === 'PUT')).toBe(true);
    });
  });

  it('load failure: empty state with a working retry', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok({ error: 'boom' }, 500))
      .mockResolvedValueOnce(ok({ error: 'boom' }, 500));
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    expect(await screen.findByText('订阅信息加载失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('never renders the forbidden copy (自动续费 / 连续包月 / 自动扣款 / 取消订阅 / 恢复订阅)', async () => {
    vi.stubGlobal('fetch', stubFetch(PLANS, EXPIRED));

    render(<SubscriptionView />);

    await screen.findByTestId('subscription-tier-grid');
    const html = document.body.innerHTML;
    expect(html).not.toContain('自动续费');
    expect(html).not.toContain('连续包月');
    expect(html).not.toContain('自动扣款');
    expect(html).not.toContain('取消订阅');
    expect(html).not.toContain('恢复订阅');
  });

  it('reports status changes back to the shell', async () => {
    vi.stubGlobal('fetch', stubFetch(PLANS, ACTIVE));
    const onSubscriptionChange = vi.fn();

    render(<SubscriptionView onSubscriptionChange={onSubscriptionChange} />);

    await screen.findByTestId('subscription-badge');
    expect(onSubscriptionChange).toHaveBeenCalled();
    expect(onSubscriptionChange.mock.calls[0][0].subscribed).toBe(true);
  });
});
