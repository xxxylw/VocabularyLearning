import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VocabApp } from './VocabApp';
import { setSessionToken } from './session';

// Batch 3 挂账项: the authed auth-route 分流 (subscribed → /today,
// not subscribed → /subscription), the /subscription route rendering,
// the guest → login redirect, and the account-menu subscription row.
// App is mocked away — its own integration tests render it directly.

vi.mock('./App', () => ({
  App: () => <div data-testid="study-app" />
}));

function ok(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const USER = { id: '1', email: 'user@example.com', emailVerified: true, isSuper: false };

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

const PLAN = { plan: 'monthly', priceCents: 10, currency: 'CNY', period: 'month' };

function stubSessionFetch(status: typeof NOT_SUBSCRIBED | typeof ACTIVE) {
  return vi.fn().mockImplementation((url: string) => {
    if (url === '/api/auth/me') {
      return Promise.resolve(ok(USER));
    }
    if (url === '/api/subscription/me') {
      return Promise.resolve(ok(status));
    }
    if (url === '/api/subscription/plan') {
      return Promise.resolve(ok(PLAN));
    }
    return Promise.resolve(ok({}));
  });
}

describe('VocabApp subscription routing (batch 3)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('sends a not-subscribed authed user on an auth route to /subscription', async () => {
    setSessionToken('token-1');
    window.location.hash = '#/login';
    vi.stubGlobal('fetch', stubSessionFetch(NOT_SUBSCRIBED));

    render(<VocabApp />);

    await waitFor(() => {
      expect(window.location.hash).toBe('#/subscription');
    });
  });

  it('sends a subscribed authed user on an auth route to /today', async () => {
    setSessionToken('token-1');
    window.location.hash = '#/register';
    vi.stubGlobal('fetch', stubSessionFetch(ACTIVE));

    render(<VocabApp />);

    await waitFor(() => {
      expect(window.location.hash).toBe('#/today');
    });
  });

  it('renders the study app for authed users on study routes', async () => {
    setSessionToken('token-1');
    window.location.hash = '#/';
    vi.stubGlobal('fetch', stubSessionFetch(ACTIVE));

    render(<VocabApp />);

    expect(await screen.findByTestId('study-app')).toBeInTheDocument();
  });

  it('redirects a guest on /subscription to the login page with next', async () => {
    window.location.hash = '#/subscription';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(ok({}))
    );

    render(<VocabApp />);

    await waitFor(() => {
      expect(window.location.hash).toBe('#/login?next=%2Fsubscription');
    });
  });

  it('renders the subscription page for authed users on /subscription', async () => {
    setSessionToken('token-1');
    window.location.hash = '#/subscription';
    vi.stubGlobal('fetch', stubSessionFetch(NOT_SUBSCRIBED));

    render(<VocabApp />);

    expect(await screen.findByText('/ 月')).toBeInTheDocument();
    expect(screen.getByText('.1')).toBeInTheDocument();
    expect(screen.queryByTestId('study-app')).toBeNull();
  });

  it('shows the subscription status row in the account menu', async () => {
    const user = userEvent.setup();
    setSessionToken('token-1');
    window.location.hash = '#/';
    vi.stubGlobal('fetch', stubSessionFetch(ACTIVE));

    render(<VocabApp />);

    await screen.findByTestId('study-app');
    await user.click(screen.getByRole('button', { name: '账号' }));

    expect(screen.getByText('订阅高')).toBeInTheDocument();
    expect(screen.getByText('user@example.com')).toBeInTheDocument();
  });

  it('shows 未订阅 for a not-subscribed non-super account', async () => {
    const user = userEvent.setup();
    setSessionToken('token-1');
    window.location.hash = '#/';
    vi.stubGlobal('fetch', stubSessionFetch(NOT_SUBSCRIBED));

    render(<VocabApp />);

    await screen.findByTestId('study-app');
    await user.click(screen.getByRole('button', { name: '账号' }));

    expect(screen.getByText('未订阅')).toBeInTheDocument();
  });
});
