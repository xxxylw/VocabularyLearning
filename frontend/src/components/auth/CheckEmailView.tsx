import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchEmailStatus, resendVerification, verifyEmailCode } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  CodeInput,
  EnvelopeIcon,
  Toast,
  useCooldown,
  useFlash
} from './shared';

type CheckEmailViewProps = {
  email: string | null;
  // from=register: registration just succeeded, so the /check-email
  // landing carries the success banner (replaces the old toast, which
  // a hash navigation could eat before it was ever seen).
  notice?: string | null;
};

const POLL_INTERVAL_MS = 5000;
const VERIFIED_REDIRECT_DELAY_MS = 5000;

// C-05a + C-01a: the post-registration landing page. The activation is
// now a 6-digit code typed right here (auto-submits when the sixth
// digit lands, paste supported) — there is no link and no 打开邮箱
// hand-off anymore. The email comes from the URL (?email=…); the page
// still polls GET /api/auth/email-status every 5s so a user activating
// in another tab sees this page flip to 已激活 automatically.
export function CheckEmailView({ email, notice }: CheckEmailViewProps) {
  const [code, setCode] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isVerified, setIsVerified] = useState(false);
  const [toastMessage, showToast] = useFlash();
  const cooldown = useCooldown();
  const submittedCodeRef = useRef<string | null>(null);

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

  // Auto-submit: the moment the sixth digit lands the code goes to the
  // backend — no extra button press (PM C-01a: 自动提交). A ref guards
  // against React double-firing the effect for the same code.
  useEffect(() => {
    if (code.length !== 6 || email === null || email === '' || isVerified) {
      return;
    }
    if (submittedCodeRef.current === code) {
      return;
    }
    submittedCodeRef.current = code;
    setIsSubmitting(true);
    setSubmitError(null);
    verifyEmailCode(email, code)
      .then(() => {
        setIsVerified(true);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError) {
          setSubmitError(error.message !== '' ? error.message : '验证码错误，请重试');
        } else {
          setSubmitError('网络异常，请稍后重试');
        }
        // Clear the boxes so the user can retype without backspacing six times.
        setCode('');
        submittedCodeRef.current = null;
      })
      .finally(() => {
        setIsSubmitting(false);
      });
  }, [code, email, isVerified]);

  function handleResend() {
    // In-flight guard (QA C-01a finding): without it a double click
    // fires two resend requests and two success toasts.
    if (email === null || email === '' || cooldown.isCooling || isSubmitting || isResending) {
      return;
    }
    setSubmitError(null);
    setIsResending(true);
    resendVerification(email)
      .then(() => {
        // C-05a spec: the resend voids the previous code server-side,
        // so the toast must say so instead of a generic "已重新发送".
        showToast('新验证码已发送，旧验证码已失效');
        cooldown.start(60);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 429) {
          setSubmitError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
        } else {
          setSubmitError('重发失败，请稍后再试');
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
          我们已把 6 位验证码发到{' '}
          <strong className="auth-email-echo">{email !== null && email !== '' ? email : '你的邮箱'}</strong>
          {email !== null && email !== '' && !isVerified ? (
            <>
              {' '}
              <button
                className="auth-inline-link"
                type="button"
                onClick={() =>
                  email !== null && email !== ''
                    ? navigate(`/register?email=${encodeURIComponent(email)}`)
                    : navigate('/register')
                }
              >
                修改邮箱
              </button>
            </>
          ) : null}
          ，在下方输入即可完成激活
        </>
      }
      icon={<EnvelopeIcon />}
    >
      {notice !== undefined && notice !== null && notice !== '' ? (
        <p className="auth-notice-banner" role="status">
          {notice}
        </p>
      ) : null}

      {isVerified ? (
        <p className="auth-verified-badge" role="status">
          已激活
        </p>
      ) : (
        <CodeInput
          idPrefix="check-email"
          value={code}
          onChange={(next) => setCode(next)}
          disabled={isSubmitting}
          hasError={submitError !== null}
        />
      )}

      {submitError !== null ? (
        <p className="auth-inline-error auth-form-error" role="alert">
          {submitError}
        </p>
      ) : null}

      {isVerified ? (
        <button
          className="auth-ghost-cta"
          type="button"
          onClick={() => navigate(`/login?from=verify&email=${encodeURIComponent(email ?? '')}`)}
        >
          去登录
        </button>
      ) : (
        <>
          {cooldown.isCooling ? (
            <p className="auth-countdown">{cooldown.remaining}s 后可重新发送</p>
          ) : (
            <p className="auth-countdown auth-countdown-ready">可以重新发送了</p>
          )}
          <button
            className="auth-ghost-cta"
            type="button"
            disabled={cooldown.isCooling || isResending}
            onClick={() => handleResend()}
          >
            没收到？重发验证码
          </button>
        </>
      )}

      <p className="auth-note">
        验证码 10 分钟内有效；错误 5 次后需重新获取；没收到请检查垃圾邮件箱
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
