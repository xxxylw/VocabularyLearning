import { useState } from 'react';
import { ApiError, requestPasswordReset } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  EnvelopeIcon,
  Spinner,
  Toast,
  isValidEmailFormat,
  openMailClient,
  useCooldown,
  useFlash
} from './shared';

// C-05b: two states on one route — the email entry form first, then the
// "reset email sent" intermediate page (envelope + countdown + resend +
// back-to-login). No email-status polling here: unlike verification
// there is no landing state on this page to poll for.
export function ForgotPasswordView({ initialEmail }: { initialEmail?: string }) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [toastMessage, showToast] = useFlash();
  const cooldown = useCooldown();

  const emailValid = isValidEmailFormat(email);

  async function submitResetRequest(target: string): Promise<void> {
    setIsSubmitting(true);
    setFormError(null);
    try {
      await requestPasswordReset(target);
      setSentTo(target);
      cooldown.start(60);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
        } else if (error.status === 503) {
          setFormError('重置邮件发送失败，请稍后重试');
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('网络异常，请稍后重试');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!emailValid || isSubmitting) {
      return;
    }
    await submitResetRequest(email.trim());
  }

  function handleResend() {
    if (sentTo === null || cooldown.isCooling || isResending) {
      return;
    }
    setIsResending(true);
    setFormError(null);
    requestPasswordReset(sentTo)
      .then(() => {
        showToast('已重新发送');
        cooldown.start(60);
      })
      .catch(() => {
        setFormError('重发失败，请稍后再试');
      })
      .finally(() => {
        setIsResending(false);
      });
  }

  if (sentTo === null) {
    return (
      <AuthCard eyebrow="VOCABULARYLEARNING" title="忘记密码" subtitle="输入注册邮箱，我们会发送重置链接">
        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <div className="auth-field">
            <label htmlFor="forgot-email">邮箱</label>
            <input
              id="forgot-email"
              className={emailError !== undefined ? 'auth-input has-error' : 'auth-input'}
              type="email"
              inputMode="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              disabled={isSubmitting}
              onChange={(event) => setEmail(event.target.value)}
              onBlur={() =>
                setEmailError(email !== '' && !emailValid ? '邮箱格式不正确' : undefined)
              }
            />
            {emailError !== undefined ? (
              <p className="auth-inline-error">{emailError}</p>
            ) : null}
          </div>

          {formError !== null ? (
            <p className="auth-inline-error auth-form-error" role="alert">
              {formError}
            </p>
          ) : null}

          <button
            className="auth-cta"
            type="submit"
            disabled={!emailValid || isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Spinner /> 发送中…
              </>
            ) : (
              '发送重置邮件'
            )}
          </button>
        </form>

        <p className="auth-switch">
          想起密码了？
          <button className="auth-text-link" type="button" onClick={() => navigate('/login')}>
            回登录
          </button>
        </p>
        <Toast message={toastMessage} />
      </AuthCard>
    );
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="查收你的重置邮件"
      subtitle={
        <>
          我们已把重置链接发到 <strong className="auth-email-echo">{sentTo}</strong>，点链接设置新密码
        </>
      }
      icon={<EnvelopeIcon />}
    >
      {cooldown.isCooling ? (
        <p className="auth-countdown">{cooldown.remaining}s 后可重新发送</p>
      ) : (
        <p className="auth-countdown auth-countdown-ready">可以重新发送了</p>
      )}

      {formError !== null ? (
        <p className="auth-inline-error auth-form-error" role="alert">
          {formError}
        </p>
      ) : null}

      <button className="auth-cta" type="button" onClick={() => openMailClient()}>
        打开邮箱
      </button>

      <button
        className="auth-ghost-cta"
        type="button"
        disabled={cooldown.isCooling || isResending}
        onClick={() => handleResend()}
      >
        {isResending ? '重发中…' : '没收到？重发邮件'}
      </button>

      <p className="auth-note">链接 1 小时内有效，仅可使用一次</p>

      <p className="auth-switch">
        想起密码了？
        <button className="auth-text-link" type="button" onClick={() => navigate('/login')}>
          回登录
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
