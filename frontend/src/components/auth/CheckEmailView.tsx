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
    if (email === null || email === '' || cooldown.isCooling || isSubmitting) {
      return;
    }
    setSubmitError(null);
    resendVerification(email)
      .then(() => {
        showToast('已重新发送');
        cooldown.start(60);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 429) {
          setSubmitError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
        } else {
          setSubmitError('重发失败，请稍后再试');
        }
      });
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="输入验证码"
      subtitle={
        <>
          验证码已发到{' '}
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
          ，输入邮件中的 6 位数字验证码激活账号
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
            disabled={cooldown.isCooling}
            onClick={() => handleResend()}
          >
            没收到？重新发送
          </button>
        </>
      )}

      <p className="auth-note">验证码 10 分钟内有效；错误 5 次后需重新获取</p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
