import { useState } from 'react';
import { ApiError, requestPasswordReset } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  Spinner,
  Toast,
  isValidEmailFormat,
  useFlash
} from './shared';

// C-05b + C-01a: the email-entry step of the reset flow. Submitting
// makes the backend email a 6-digit code; the user then enters that
// code (plus the new password) on /reset-password — this page hops
// straight there with the address prefilled, so there is no
// intermediate "sent" state (and no 打开邮箱 hand-off).
export function ForgotPasswordView({ initialEmail }: { initialEmail?: string }) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toastMessage, showToast] = useFlash();

  const emailValid = isValidEmailFormat(email);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!emailValid || isSubmitting) {
      return;
    }
    setFormError(null);
    setIsSubmitting(true);
    try {
      await requestPasswordReset(email.trim());
      showToast('验证码已发送');
      navigate(`/reset-password?email=${encodeURIComponent(email.trim())}`);
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

  return (
    <AuthCard eyebrow="VOCABULARYLEARNING" title="忘记密码" subtitle="输入注册邮箱，我们会发送 6 位数字验证码">
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
            '发送验证码'
          )}
        </button>
      </form>

      <p className="auth-note">验证码 10 分钟内有效，错误 5 次后需重新获取</p>

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
