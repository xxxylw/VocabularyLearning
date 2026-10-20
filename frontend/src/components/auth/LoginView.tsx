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
          setFormError('Incorrect email or password.');
        } else if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : 'Too many requests. Please try again later.');
        } else if (error.status >= 500) {
          setFormError('Network error. Please try again later.');
          setIsNetworkError(true);
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('Network error. Please try again later.');
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
      setFieldErrors((prev) => ({ ...prev, email: 'Invalid email address' }));
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
        // C-05a spec copy (designer P2-② reuse): the resend voids the
        // previous code server-side, so say so instead of 已重新发送.
        showToast('A new code has been sent; the previous one is no longer valid.');
        cooldown.start(60);
      })
      .catch(() => {
        setResendError('Failed to resend. Please try again later.');
      })
      .finally(() => {
        setIsResending(false);
      });
  }

  return (
    <AuthCard
      eyebrow="FUSIO"
      title="Log In"
      subtitle="Continue your study progress with your email"
    >
      {notice !== undefined && notice !== null && notice !== '' ? (
        <p className="auth-notice-banner" role="status">
          {notice}
        </p>
      ) : null}

      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="login-email">Email</label>
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
          <label htmlFor="login-password">Password</label>
          <PasswordField
            id="login-password"
            autoComplete="current-password"
            placeholder="Enter your password"
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
              Forgot your password?
            </button>
          </div>
        </div>

        {notVerified ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            This email isn't verified yet. Check your inbox (
            <button
              className="auth-inline-link"
              type="button"
              onClick={() => navigate(`/check-email?email=${encodeURIComponent(email.trim())}`)}
            >
              View Email
            </button>
            <span aria-hidden="true"> / </span>
            <button
              className="auth-inline-link"
              type="button"
              disabled={cooldown.isCooling || isResending}
              onClick={() => void handleResend()}
            >
              {isResending ? 'Resending…' : 'Resend'}
            </button>
            )
          </p>
        ) : null}

        {formError !== null && !notVerified ? (
          <p className="auth-inline-error auth-form-error" role="alert">
            {formError}
            {isNetworkError ? (
              <>
                {'('}
                <button
                  className="auth-inline-link"
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => void submitLogin()}
                >
                  Retry
                </button>
                {')'}
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
              <Spinner /> Logging in…
            </>
          ) : (
            'Log In'
          )}
        </button>
      </form>

      <p className="auth-switch">
        No account yet?
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
          Sign Up
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
