import { useEffect, useState } from 'react';
import { App } from './App';
import { fetchCurrentUser, logout } from './api';
import type { AuthUser } from './api';
import { navigate, routeToString, useHashRoute, isAuthRoute } from './router';
import { clearSessionToken, getSessionToken, setSessionToken } from './session';
import { CheckEmailView } from './components/auth/CheckEmailView';
import { ForgotPasswordView } from './components/auth/ForgotPasswordView';
import { LoginView } from './components/auth/LoginView';
import { RegisterView } from './components/auth/RegisterView';
import { ResetPasswordView } from './components/auth/ResetPasswordView';
import { VerifyEmailView } from './components/auth/VerifyEmailView';
import { Spinner } from './components/auth/shared';

type SessionState =
  | { status: 'checking' }
  | { status: 'authed'; user: AuthUser }
  | { status: 'guest' };

// v2 cloud shell: hash routing (the verification emails link to
// /#/verify-email?token=…, so a hash router is mandatory) plus the auth
// guard from spec C-04:
//   - guest visiting a study route → /login?next=<target>
//   - logged-in user visiting an auth route → back to the study app
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

  // Route guard. Runs only after the session is resolved so a reload
  // with a valid token doesn't bounce through /login first.
  useEffect(() => {
    if (session.status === 'checking') {
      return;
    }
    if (session.status === 'guest' && !isAuthRoute(route.path)) {
      navigate(`/login?next=${encodeURIComponent(routeToString(route))}`);
    }
    if (session.status === 'authed' && isAuthRoute(route.path)) {
      navigate('/');
    }
  }, [session, route]);

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
            notice={query.get('from') === 'register' ? '账号已创建，去邮箱激活' : null}
          />
        );
      case '/forgot-password':
        return <ForgotPasswordView initialEmail={query.get('email') ?? undefined} />;
      case '/reset-password':
        return <ResetPasswordView token={query.get('token') ?? ''} />;
      case '/verify-email':
        return <VerifyEmailView token={query.get('token') ?? ''} />;
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

  return (
    <>
      <AccountArea user={session.user} onLogout={handleLogout} />
      <App />
    </>
  );
}

// C-04 spec: a global account area pinned top-right of the app shell
// with the signed-in email and 退出登录.
function AccountArea({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
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
          <p className="account-menu-email">{user.email}</p>
          {user.isSuper ? <p className="account-menu-plan">super 账号</p> : null}
          <button type="button" className="account-menu-logout" role="menuitem" onClick={onLogout}>
            退出登录
          </button>
        </div>
      ) : null}
    </div>
  );
}
