import { useEffect, useState } from 'react';
import { ApiError, fetchEmailStatus, resendVerification } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  EnvelopeIcon,
  Toast,
  openMailClient,
  useCooldown,
  useFlash
} from './shared';

type CheckEmailViewProps = {
  email: string | null;
};

const POLL_INTERVAL_MS = 5000;
const VERIFIED_REDIRECT_DELAY_MS = 5000;

// C-05a: the "check your inbox" intermediate page after registration.
// Email comes from the URL (?email=…) — it is data, not visual, and the
// same copy is reused by the login page's 403 email_not_verified branch.
// Polls GET /api/auth/email-status every 5s so a user activating the
// link in another tab sees this page flip to 已激活 automatically.
export function CheckEmailView({ email }: CheckEmailViewProps) {
  const [isResending, setIsResending] = useState(false);
  const [resendError, setResendError] = useState<string | null>(null);
  const [isVerified, setIsVerified] = useState(false);
  const [toastMessage, showToast] = useFlash();
  const cooldown = useCooldown();

  useEffect(() => {
    cooldown.start(60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (email === null || email === '' || isVerified) {
      return;
    }
    let cancelled = false;
    const poll = window.setInterval(() => {
      if (document.visibilityState !== undefined && document.visibilityState === 'hidden') {
        return;
      }
      fetchEmailStatus(email)
        .then((status) => {
          if (!cancelled && status.verified) {
            setIsVerified(true);
          }
        })
        .catch(() => {
          // Polling is opportunistic; transient failures are ignored.
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
    };
  }, [email, isVerified]);

  useEffect(() => {
    if (!isVerified) {
      return;
    }
    const timer = window.setTimeout(() => {
      navigate(`/login?from=verify&email=${encodeURIComponent(email ?? '')}`);
    }, VERIFIED_REDIRECT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [isVerified, email]);

  function handleResend() {
    if (email === null || email === '' || cooldown.isCooling || isResending) {
      return;
    }
    setIsResending(true);
    setResendError(null);
    resendVerification(email)
      .then(() => {
        showToast('已重新发送');
        cooldown.start(60);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 429) {
          setResendError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
        } else {
          setResendError('重发失败，请稍后再试');
        }
      })
      .finally(() => {
        setIsResending(false);
      });
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="查收你的验证邮件"
      subtitle={
        <>
          我们已把激活链接发到{' '}
          <strong className="auth-email-echo">{email !== null && email !== '' ? email : '你的邮箱'}</strong>
          {email !== null && email !== '' && !isVerified ? (
            <>
              {' '}
              <button
                className="auth-inline-link"
                type="button"
                onClick={() => navigate('/register')}
              >
                修改邮箱
              </button>
            </>
          ) : null}
          ，点链接即可激活账号
        </>
      }
      icon={<EnvelopeIcon />}
    >
      {isVerified ? (
        <p className="auth-verified-badge" role="status">
          已激活
        </p>
      ) : cooldown.isCooling ? (
        <p className="auth-countdown">{cooldown.remaining}s 后可重新发送</p>
      ) : (
        <p className="auth-countdown auth-countdown-ready">可以重新发送了</p>
      )}

      {resendError !== null ? (
        <p className="auth-inline-error auth-form-error" role="alert">
          {resendError}
        </p>
      ) : null}

      <button className="auth-cta" type="button" onClick={() => openMailClient()}>
        打开邮箱
      </button>

      {isVerified ? (
        <button
          className="auth-ghost-cta"
          type="button"
          onClick={() => navigate(`/login?from=verify&email=${encodeURIComponent(email ?? '')}`)}
        >
          去登录
        </button>
      ) : (
        <button
          className="auth-ghost-cta"
          type="button"
          disabled={cooldown.isCooling || isResending}
          onClick={() => handleResend()}
        >
          {isResending ? '重发中…' : '没收到？重发邮件'}
        </button>
      )}

      <p className="auth-note">链接 1 小时内有效；没收到请检查垃圾邮件箱</p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
