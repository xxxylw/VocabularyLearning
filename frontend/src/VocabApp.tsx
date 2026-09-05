import { useEffect, useState } from 'react';
import { App } from './App';
import { fetchCurrentUser, fetchSubscriptionMe, logout } from './api';
import type { AuthUser, SubscriptionStatus } from './api';
import { navigate, routeToString, useHashRoute, isAuthRoute } from './router';
import { clearSessionToken, getSessionToken, setSessionToken } from './session';
import { CheckEmailView } from './components/auth/CheckEmailView';
import { ForgotPasswordView } from './components/auth/ForgotPasswordView';
import { LoginView } from './components/auth/LoginView';
import { RegisterView } from './components/auth/RegisterView';
import { ResetPasswordView } from './components/auth/ResetPasswordView';
import { SubscriptionView } from './components/auth/SubscriptionView';
import { VerifyEmailView } from './components/auth/VerifyEmailView';
import { Spinner } from './components/auth/shared';

type SessionState =
  | { status: 'checking' }
  | { status: 'authed'; user: AuthUser; subscription?: SubscriptionStatus }
  | { status: 'guest' };

// v2 cloud shell: hash routing (the verification emails link to
// /#/verify-email?token=…, so a hash router is mandatory) plus the auth
// guard from spec C-04:
//   - guest visiting a study route (incl. /subscription) → /login?next=<target>
//   - logged-in user visiting an auth route → /today (subscribed) or
//     /subscription (not subscribed) — batch 3 挂账项, subscription is
//     display-only and never gates study features
// App.tsx itself stays untouched — its six integration tests render it
// directly and must keep passing.
export function VocabApp() {
  const route = useHashRoute();
  const [session, setSession] = useState<SessionState>({ status: 'checking' });

  // Restore the session on mount: token → GET /api/auth/me; stale or
  // rejected tokens are dropped so the user lands on /login cleanly.
  useEffect(() => {
    const token = getSessionToken();
    if (token === null) {
      setSession({ status: 'guest' });
      return;
    }
    let cancelled = false;
    fetchCurrentUser()
      .then((user) => {
        if (!cancelled) {
          setSession({ status: 'authed', user });
        }
      })
      .catch(() => {
        clearSessionToken();
        if (!cancelled) {
          setSession({ status: 'guest' });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the subscription status for the authed user — feeds the
  // auth-route 分流 and the account-menu status row. A failed lookup
  // degrades to "not subscribed" so the 分流 still resolves (the
  // subscription page itself has a retry for a real fetch).
  useEffect(() => {
    if (session.status !== 'authed') {
      return;
    }
    let cancelled = false;
    fetchSubscriptionMe()
      .then((status) => {
        if (!cancelled) {
          setSession((current) =>
            current.status === 'authed' ? { ...current, subscription: status } : current
          );
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSession((current) =>
            current.status === 'authed'
              ? {
                  ...current,
                  subscription: {
                    subscribed: false,
                    plan: null,
                    status: null,
                    startedAt: null,
                    expiresAt: null,
                    autoRenew: null,
                    source: null
                  }
                }
              : current
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [session.status]);

  // Route guard. Runs only after the session is resolved so a reload
  // with a valid token doesn't bounce through /login first.
  useEffect(() => {
    if (session.status === 'checking') {
      return;
    }
    if (session.status === 'guest' && !isAuthRoute(route.path)) {
      navigate(`/login?next=${encodeURIComponent(routeToString(route))}`);
    }
    if (
      session.status === 'authed' &&
      isAuthRoute(route.path) &&
      session.subscription !== undefined
    ) {
      // 认证路由分流（batch 3）：已订阅 → /today，未订阅 → /subscription。
      // Waits for the subscription lookup so the destination is stable.
      navigate(session.subscription.subscribed ? '/today' : '/subscription');
    }
  }, [session, route]);

  function handleSubscriptionChange(next: SubscriptionStatus) {
    setSession((current) =>
      current.status === 'authed' ? { ...current, subscription: next } : current
    );
  }

  function handleLoginSuccess(token: string, user: AuthUser) {
    setSessionToken(token);
    setSession({ status: 'authed', user });
    const next =
      route.query.get('next') !== null && route.query.get('next') !== ''
        ? String(route.query.get('next'))
        : null;
    // next must stay inside the app — never bounce to an arbitrary hash.
    navigate(next !== null && next.startsWith('/') ? next : '/');
  }

  function handleLogout() {
    void logout().catch(() => {
      // The session is being discarded client-side regardless.
    });
    clearSessionToken();
    setSession({ status: 'guest' });
    navigate('/login');
  }

  if (session.status === 'checking') {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <p className="auth-subtitle">
            <Spinner /> 正在进入…
          </p>
        </section>
      </main>
    );
  }

  if (session.status === 'guest' && isAuthRoute(route.path)) {
    const query = route.query;
    switch (route.path) {
      case '/login':
        return (
          <LoginView
            initialEmail={query.get('email') ?? undefined}
            notice={
              query.get('from') === 'verify'
                ? '邮箱已激活，请登录'
                : query.get('from') === 'reset'
                  ? '请用新密码登录'
                  : null
            }
            next={query.get('next')}
            autoFocusPassword={
              query.get('from') === 'verify' || query.get('from') === 'reset'
            }
            onLoginSuccess={handleLoginSuccess}
          />
        );
      case '/register':
        return <RegisterView initialEmail={query.get('email') ?? undefined} />;
      case '/check-email':
        return (
          <CheckEmailView
            email={query.get('email')}
            notice={query.get('from') === 'register' ? '账号已创建，验证码已发送至你的邮箱' : null}
          />
        );
      case '/forgot-password':
        return <ForgotPasswordView initialEmail={query.get('email') ?? undefined} />;
      case '/reset-password':
        return <ResetPasswordView initialEmail={query.get('email') ?? undefined} />;
      case '/verify-email':
        return <VerifyEmailView email={query.get('email') ?? undefined} />;
      default:
        return null;
    }
  }

  if (session.status === 'guest') {
    // Study route while logged out — the guard effect above is
    // redirecting; show the generic loader meanwhile.
    return (
      <main className="auth-page">
        <section className="auth-card">
          <p className="auth-subtitle">
            <Spinner /> 正在跳转登录…
          </p>
        </section>
      </main>
    );
  }

  if (route.path === '/subscription') {
    return (
      <SubscriptionView onSubscriptionChange={handleSubscriptionChange} />
    );
  }

  if (isAuthRoute(route.path)) {
    // Authed user on an auth route while the subscription lookup is
    // still in flight — the guard effect will 分流 as soon as it lands.
    return (
      <main className="auth-page">
        <section className="auth-card">
          <p className="auth-subtitle">
            <Spinner /> 正在进入…
          </p>
        </section>
      </main>
    );
  }

  return (
    <>
      <AccountArea
        user={session.user}
        subscription={session.subscription}
        onLogout={handleLogout}
      />
      <App />
    </>
  );
}

// C-04 spec: a global account area pinned top-right of the app shell
// with the signed-in email, the subscription status row (C-10 挂账项,
// badge 样式同订阅页) and 退出登录. ≤760px the panel becomes a
// bottom sheet (偏差 D6 收口).
function AccountArea({
  user,
  subscription,
  onLogout
}: {
  user: AuthUser;
  subscription?: SubscriptionStatus;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    const close = () => setOpen(false);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [open]);

  return (
    <div className="account-menu" data-testid="account-menu">
      <button
        type="button"
        className="account-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="账号"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
          <circle cx="14" cy="9.5" r="4.25" stroke="#486f83" strokeWidth="2" />
          <path
            d="M5.5 23.5c1.6-3.9 4.7-6 8.5-6s6.9 2.1 8.5 6"
            stroke="#486f83"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </button>
      {open ? (
        <div className="account-menu-panel" role="menu" onClick={(event) => event.stopPropagation()}>
          <span className="account-menu-handle" aria-hidden="true" />
          <p className="account-menu-email">{user.email}</p>
          {user.isSuper ? (
            <p className="account-menu-plan">super 账号</p>
          ) : subscription !== undefined ? (
            subscription.subscribed ? (
              <p className="account-menu-plan">
                <span className="subscription-badge">订阅高</span>
              </p>
            ) : (
              <p className="account-menu-plan account-menu-plan-muted">未订阅</p>
            )
          ) : null}
          <button type="button" className="account-menu-logout" role="menuitem" onClick={onLogout}>
            退出登录
          </button>
        </div>
      ) : null}
    </div>
  );
}
