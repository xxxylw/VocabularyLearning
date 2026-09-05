import { useState } from 'react';
import { ApiError, register } from '../../api';
import { navigate } from '../../router';
import { AuthCard, PasswordField, Spinner, isValidEmailFormat, isValidPassword, useCooldown, PASSWORD_POLICY_HINT } from './shared';

type RegisterViewProps = {
  initialEmail?: string;
};

type FieldErrors = {
  email?: string;
  password?: string;
  confirm?: string;
};

// C-02: register does NOT auto-login (the user must click the email
// activation link first). On success we hand off to /check-email.
// On 503 email_send_failed the backend rolled the half-created account
// back, so "再发一次" re-runs the whole registration — retrying the
// resend endpoint alone would hit a user that no longer exists.
export function RegisterView({ initialEmail }: RegisterViewProps) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [showRetry, setShowRetry] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const cooldown = useCooldown();

  const emailValid = isValidEmailFormat(email);
  const passwordsMatch = password !== '' && password === confirmPassword;
  const canSubmit = emailValid && isValidPassword(password) && passwordsMatch && !isSubmitting;

  async function submitRegistration(): Promise<void> {
    if (!canSubmit) {
      return;
    }
    setFormError(null);
    setShowRetry(false);
    setFieldErrors({});
    setIsSubmitting(true);

    try {
      await register(email.trim(), password);
      // C-02: no auto-login. The success feedback lands on the
      // /check-email page itself (banner via from=register) instead of
      // a toast that a navigation could eat — see CheckEmailView.
      navigate(`/check-email?email=${encodeURIComponent(email.trim())}&from=register`);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 409) {
          setFieldErrors((prev) => ({ ...prev, email: '该邮箱已注册' }));
        } else if (error.status === 503) {
          setFormError('验证邮件发送失败，请稍后重试');
          setShowRetry(true);
        } else if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
          cooldown.start(60);
        } else if (error.status >= 500) {
          setFormError('网络异常，请稍后重试');
          setShowRetry(true);
        } else if (error.status === 400) {
          setFormError(error.message);
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('网络异常，请稍后重试');
        setShowRetry(true);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitRegistration();
  }

  function validateEmailField() {
    setFieldErrors((prev) => ({
      ...prev,
      email: email !== '' && !emailValid ? '邮箱格式不正确' : undefined
    }));
  }

  function validatePasswordField() {
    setFieldErrors((prev) => ({
      ...prev,
      password: password !== '' && !isValidPassword(password) ? PASSWORD_POLICY_HINT : undefined
    }));
  }

  function validateConfirmField() {
    setFieldErrors((prev) => ({
      ...prev,
      confirm:
        confirmPassword !== '' && !passwordsMatch ? '两次输入的密码不一致' : undefined
    }));
  }

  return (
    <AuthCard eyebrow="VOCABULARYLEARNING" title="创建账号" subtitle="开始云端背单词">
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="register-email">邮箱</label>
          <input
            id="register-email"
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
            <p className="auth-inline-error">
              <span>{fieldErrors.email}</span>
              {fieldErrors.email === '该邮箱已注册' ? (
                <>
                  （
                  <button
                    className="auth-inline-link"
                    type="button"
                    onClick={() =>
                      navigate(`/login?email=${encodeURIComponent(email.trim())}`)
                    }
                  >
                    去登录
                  </button>
                  ）
                </>
              ) : null}
            </p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="register-password">密码</label>
          <PasswordField
            id="register-password"
            autoComplete="new-password"
            placeholder="设置密码（至少 8 位，含字母和数字）"
            value={password}
            hasError={fieldErrors.password !== undefined}
            disabled={isSubmitting}
            onChange={(event) => setPassword(event.target.value)}
            onBlur={validatePasswordField}
          />
          <p className="auth-field-hint">{PASSWORD_POLICY_HINT}</p>
          {fieldErrors.password !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.password}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="register-confirm">确认密码</label>
          <PasswordField
            id="register-confirm"
            autoComplete="new-password"
            placeholder="再次输入密码"
            value={confirmPassword}
            hasError={fieldErrors.confirm !== undefined}
            disabled={isSubmitting}
            onChange={(event) => setConfirmPassword(event.target.value)}
            onBlur={validateConfirmField}
          />
          {fieldErrors.confirm !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.confirm}</p>
          ) : null}
        </div>

        {formError !== null ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            {formError}
          </p>
        ) : null}

        <button className="auth-cta" type="submit" disabled={!canSubmit}>
          {isSubmitting ? (
            <>
              <Spinner /> 创建中…
            </>
          ) : (
            '创建账号'
          )}
        </button>

        {showRetry ? (
          <button
            className="auth-ghost-cta"
            type="button"
            disabled={cooldown.isCooling || isSubmitting}
            onClick={() => void submitRegistration()}
          >
            {cooldown.isCooling ? `再发一次（${cooldown.remaining}s 后可用）` : '再发一次'}
          </button>
        ) : null}
      </form>

      <p className="auth-switch">
        已有账号？
        <button
          className="auth-text-link"
          type="button"
          onClick={() =>
            navigate(
              email.trim() !== '' ? `/login?email=${encodeURIComponent(email.trim())}` : '/login'
            )
          }
        >
          登录
        </button>
      </p>
    </AuthCard>
  );
}
