import { useState } from 'react';
import { ApiError, login, resendVerification } from '../../api';
import type { AuthUser } from '../../api';
import { navigate } from '../../router';
import { Spinner, Toast, isValidEmailFormat, useCooldown, useFlash, PasswordField } from './shared';
import { AuthCard } from './shared';

type LoginViewProps = {
  initialEmail?: string;
  notice?: string | null;
  next?: string | null;
  // from=verify / from=reset: the user arrives with a known-good email
  // (already prefilled), so focus the password field to save a tap.
  autoFocusPassword?: boolean;
  onLoginSuccess: (token: string, user: AuthUser) => void;
};

type FieldErrors = {
  email?: string;
  password?: string;
};

// C-01: email + password login. 403 email_not_verified gets the inline
// 去查收/重发 branch that shares copy with C-05a.
export function LoginView({
  initialEmail,
  notice,
  next,
  autoFocusPassword,
  onLoginSuccess
}: LoginViewProps) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [isNetworkError, setIsNetworkError] = useState(false);
  const [notVerified, setNotVerified] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [resendError, setResendError] = useState<string | null>(null);
  const [toastMessage, showToast] = useFlash();
  const cooldown = useCooldown();

  const emailValid = isValidEmailFormat(email);
  const canSubmit = emailValid && password !== '' && !isSubmitting;

  async function submitLogin(): Promise<void> {
    if (!canSubmit) {
      return;
    }
    setFormError(null);
    setIsNetworkError(false);
    setNotVerified(false);
    setFieldErrors({});
    setIsSubmitting(true);

    try {
      const result = await login(email.trim(), password);
      onLoginSuccess(result.token, result.user);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 403) {
          setNotVerified(true);
        } else if (error.status === 401) {
          setFormError('邮箱或密码不正确');
        } else if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
        } else if (error.status >= 500) {
          setFormError('网络异常，请稍后重试');
          setIsNetworkError(true);
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('网络异常，请稍后重试');
        setIsNetworkError(true);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitLogin();
  }

  function validateEmailField() {
    if (email !== '' && !emailValid) {
      setFieldErrors((prev) => ({ ...prev, email: '邮箱格式不正确' }));
    } else {
      setFieldErrors((prev) => ({ ...prev, email: undefined }));
    }
  }

  function handleResend() {
    if (cooldown.isCooling || isResending) {
      return;
    }
    setIsResending(true);
    setResendError(null);
    resendVerification(email.trim())
      .then(() => {
        showToast('已重新发送');
        cooldown.start(60);
      })
      .catch(() => {
        setResendError('重发失败，请稍后再试');
      })
      .finally(() => {
        setIsResending(false);
      });
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="登录"
      subtitle="用邮箱继续你的学习进度"
    >
      {notice !== undefined && notice !== null && notice !== '' ? (
        <p className="auth-notice-banner" role="status">
          {notice}
        </p>
      ) : null}

      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="login-email">邮箱</label>
          <input
            id="login-email"
            className={fieldErrors.email !== undefined ? 'auth-input has-error' : 'auth-input'}
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            disabled={isSubmitting}
            onChange={(event) => setEmail(event.target.value)}
            onBlur={validateEmailField}
          />
          {fieldErrors.email !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.email}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="login-password">密码</label>
          <PasswordField
            id="login-password"
            autoComplete="current-password"
            placeholder="输入密码"
            value={password}
            disabled={isSubmitting}
            autoFocus={autoFocusPassword}
            onChange={(event) => setPassword(event.target.value)}
          />
          <div className="auth-field-aux">
            <button
              className="auth-text-link"
              type="button"
              onClick={() => navigate('/forgot-password')}
            >
              忘记密码？
            </button>
          </div>
        </div>

        {notVerified ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            该邮箱尚未激活，请查收验证邮件（
            <button
              className="auth-inline-link"
              type="button"
              onClick={() => navigate(`/check-email?email=${encodeURIComponent(email.trim())}`)}
            >
              去查收
            </button>
            <span aria-hidden="true"> / </span>
            <button
              className="auth-inline-link"
              type="button"
              disabled={cooldown.isCooling || isResending}
              onClick={() => void handleResend()}
            >
              {isResending ? '重发中…' : '重发'}
            </button>
            ）
          </p>
        ) : null}

        {formError !== null && !notVerified ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            {formError}
            {isNetworkError ? (
              <>
                {'（'}
                <button
                  className="auth-inline-link"
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => void submitLogin()}
                >
                  重试
                </button>
                {'）'}
              </>
            ) : null}
          </p>
        ) : null}

        {resendError !== null ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            {resendError}
          </p>
        ) : null}

        <button className="auth-cta" type="submit" disabled={!canSubmit}>
          {isSubmitting ? (
            <>
              <Spinner /> 登录中…
            </>
          ) : (
            '登录'
          )}
        </button>
      </form>

      <p className="auth-switch">
        还没有账号？
        <button
          className="auth-text-link"
          type="button"
          onClick={() =>
            navigate(
              typeof next === 'string' && next !== ''
                ? `/register?next=${encodeURIComponent(next)}`
                : '/register'
            )
          }
        >
          注册
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
