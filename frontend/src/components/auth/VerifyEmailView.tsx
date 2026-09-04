import { useEffect, useState } from 'react';
import { verifyEmail } from '../../api';
import { navigate } from '../../router';
import { AuthCard, Spinner } from './shared';

type VerifyEmailViewProps = {
  token: string;
};

type VerifyState =
  | { status: 'verifying' }
  | { status: 'verified'; email: string }
  | { status: 'invalid' };

const REDIRECT_DELAY_MS = 1200;

// Landing page of the email activation link ({base}/#/verify-email?token=…).
// GET /api/auth/verify-email consumes the token, marks the account
// verified, and returns the email — then we bounce to /login with the
// address prefilled and a "邮箱已激活，请登录" notice.
export function VerifyEmailView({ token }: VerifyEmailViewProps) {
  const [state, setState] = useState<VerifyState>({ status: 'verifying' });

  useEffect(() => {
    let cancelled = false;
    verifyEmail(token)
      .then((result) => {
        if (!cancelled) {
          setState({ status: 'verified', email: result.email });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ status: 'invalid' });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (state.status !== 'verified') {
      return;
    }
    const timer = window.setTimeout(() => {
      navigate(`/login?from=verify&email=${encodeURIComponent(state.email)}`);
    }, REDIRECT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [state]);

  if (state.status === 'verifying') {
    return (
      <AuthCard eyebrow="VOCABULARYLEARNING" title="邮箱激活">
        <p className="auth-subtitle">
          <Spinner /> 正在激活你的邮箱…
        </p>
      </AuthCard>
    );
  }

  if (state.status === 'invalid') {
    return (
      <AuthCard eyebrow="VOCABULARYLEARNING" title="邮箱激活" subtitle="这个激活链接已失效或已使用">
        <p className="auth-inline-error auth-form-error" role="alert">
          链接已失效或已使用
        </p>
        <p className="auth-note">如果账号还没激活，登录时会提示重新发送验证邮件</p>
        <button
          className="auth-ghost-cta"
          type="button"
          onClick={() => navigate('/login')}
        >
          去登录
        </button>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="邮箱已激活"
      subtitle={
        <>
          <strong className="auth-email-echo">{state.email}</strong> 已完成激活，正在跳转登录页…
        </>
      }
    >
      <p className="auth-countdown" role="status">
        <Spinner /> 即将前往登录页
      </p>
    </AuthCard>
  );
}
