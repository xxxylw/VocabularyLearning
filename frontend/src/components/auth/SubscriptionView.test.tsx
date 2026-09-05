import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SubscriptionView } from './SubscriptionView';

// C-10 subscription page tests. The price must come from the plan
// payload (0.1 today, 4.99 after a config change) — never from copy —
// and the card flips wholesale between price / subscribed states with
// toasts on the mock order and mock cancel.

function ok(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const PLAN = { plan: 'monthly', priceCents: 10, currency: 'CNY', period: 'month' };

const NOT_SUBSCRIBED = {
  subscribed: false,
  plan: null,
  status: null,
  startedAt: null,
  expiresAt: null,
  autoRenew: null,
  source: null
};

const ACTIVE = {
  subscribed: true,
  plan: 'monthly',
  status: 'active',
  startedAt: '2026-09-05T00:00:00+00:00',
  expiresAt: '2026-10-05T00:00:00+00:00',
  autoRenew: true,
  source: 'mock'
};

const EXPIRED = {
  subscribed: false,
  plan: 'monthly',
  status: 'expired',
  startedAt: '2026-08-05T00:00:00+00:00',
  expiresAt: '2026-09-04T00:00:00+00:00',
  autoRenew: false,
  source: 'mock'
};

const SUPER_VIEW = {
  subscribed: true,
  plan: 'super',
  status: null,
  startedAt: null,
  expiresAt: null,
  autoRenew: null,
  source: null
};

function stubFetch(planBody: unknown, statusBody: unknown) {
  return vi
    .fn()
    .mockResolvedValueOnce(ok(planBody))
    .mockResolvedValueOnce(ok(statusBody));
}

describe('SubscriptionView', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the price strictly from the plan payload', async () => {
    const fetchMock = stubFetch(PLAN, NOT_SUBSCRIBED);
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    await screen.findByText('/ 月');
    expect(screen.getByRole('button', { name: '立即订阅' })).toBeEnabled();
    expect(screen.getByText('¥').textContent).toBe('¥');
    expect(screen.getByText('0').textContent).toBe('0');
    expect(screen.getByText('.1').textContent).toBe('.1');
    expect(screen.getByText('/ 月').textContent).toBe('/ 月');
    expect(screen.getByText('模拟订阅，不会产生真实扣款')).toBeInTheDocument();
    expect(screen.queryByTestId('subscription-badge')).toBeNull();
  });

  it('shows the 4.99 price when the backend config changes', async () => {
    const fetchMock = stubFetch({ ...PLAN, priceCents: 499 }, NOT_SUBSCRIBED);
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    await screen.findByText('.99');
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('.99')).toBeInTheDocument();
    expect(screen.queryByText('.1')).toBeNull();
  });

  it('subscribes: whole card flips to the status card with toast', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(PLAN))
      .mockResolvedValueOnce(ok(NOT_SUBSCRIBED))
      .mockResolvedValueOnce(ok(ACTIVE));
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    await screen.findByText('/ 月');
    await user.click(screen.getByRole('button', { name: '立即订阅' }));

    expect(await screen.findByText('订阅成功')).toBeInTheDocument();
    expect(screen.getByTestId('subscription-badge').textContent).toBe('订阅高');
    expect(screen.getByText('有效期至 2026-10-05')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消订阅（模拟）' })).toBeEnabled();
    expect(screen.queryByText('立即订阅')).toBeNull();
    expect(fetchMock.mock.calls[2][0]).toBe('/api/subscription/mock-order');
  });

  it('cancels: back to the price card with the mock toast', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(PLAN))
      .mockResolvedValueOnce(ok(ACTIVE))
      .mockResolvedValueOnce(
        ok({ ...ACTIVE, subscribed: false, status: 'canceled', autoRenew: false })
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    expect(await screen.findByText('取消订阅（模拟）')).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '取消订阅（模拟）' }));

    expect(await screen.findByText('已取消订阅（模拟）')).toBeInTheDocument();
    expect(screen.getByText('立即订阅')).toBeInTheDocument();
    expect(screen.queryByTestId('subscription-badge')).toBeNull();
    expect(fetchMock.mock.calls[2][0]).toBe('/api/subscription/cancel');
  });

  it('expired state: renewal hint above the price card, resubscribe allowed', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(PLAN))
      .mockResolvedValueOnce(ok(EXPIRED))
      .mockResolvedValueOnce(ok(ACTIVE));
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    expect(await screen.findByText('订阅已过期，续订后恢复云同步')).toBeInTheDocument();
    expect(screen.getByText('立即订阅')).toBeEnabled();

    await user.click(screen.getByRole('button', { name: '立即订阅' }));
    expect(await screen.findByText('订阅成功')).toBeInTheDocument();
  });

  it('super view: permanent status card without an expiry date', async () => {
    const fetchMock = stubFetch(PLAN, SUPER_VIEW);
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    expect(await screen.findByTestId('subscription-badge')).toBeInTheDocument();
    expect(screen.getByText('长期有效')).toBeInTheDocument();
    expect(screen.queryByText('立即订阅')).toBeNull();
  });

  it('load failure: empty state with a working retry', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok({}, 500))
      .mockResolvedValueOnce(ok(NOT_SUBSCRIBED))
      .mockResolvedValueOnce(ok(PLAN))
      .mockResolvedValueOnce(ok(NOT_SUBSCRIBED));
    vi.stubGlobal('fetch', fetchMock);

    render(<SubscriptionView />);

    const retry = await screen.findByRole('button', { name: '重试' });
    expect(screen.getByText('订阅信息加载失败')).toBeInTheDocument();
    expect(screen.queryByText('立即订阅')).toBeNull();

    await user.click(retry);

    await screen.findByText('/ 月');
    expect(screen.getByRole('button', { name: '立即订阅' })).toBeEnabled();
  });

  it('skip link navigates to /today without subscribing', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', stubFetch(PLAN, NOT_SUBSCRIBED));

    render(<SubscriptionView />);

    await screen.findByText('/ 月');
    await user.click(screen.getByRole('button', { name: '暂不订阅，先去背单词' }));

    expect(window.location.hash).toBe('#/today');
  });

  it('reports status changes back to the shell', async () => {
    const onSubscriptionChange = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(ok(PLAN))
        .mockResolvedValueOnce(ok(NOT_SUBSCRIBED))
        .mockResolvedValueOnce(ok(ACTIVE))
    );

    render(<SubscriptionView onSubscriptionChange={onSubscriptionChange} />);

    await screen.findByText('/ 月');
    fireEvent.click(screen.getByRole('button', { name: '立即订阅' }));

    await waitFor(() => {
      expect(onSubscriptionChange).toHaveBeenCalledWith(
        expect.objectContaining({ subscribed: true, status: 'active' })
      );
    });
  });
});
