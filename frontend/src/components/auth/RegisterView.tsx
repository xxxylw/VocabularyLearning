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
          setFieldErrors((prev) => ({ ...prev, email: 'This email is already registered.' }));
        } else if (error.status === 503) {
          setFormError('Failed to send the verification email. Please try again later.');
          setShowRetry(true);
        } else if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : 'Too many requests. Please try again later.');
          cooldown.start(60);
        } else if (error.status >= 500) {
          setFormError('Network error. Please try again later.');
          setShowRetry(true);
        } else if (error.status === 400) {
          setFormError(error.message);
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('Network error. Please try again later.');
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
      email: email !== '' && !emailValid ? 'Invalid email address' : undefined
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
        confirmPassword !== '' && !passwordsMatch ? 'Passwords do not match.' : undefined
    }));
  }

  return (
    <AuthCard eyebrow="FUSIO" title="Create Account" subtitle="Start building your vocabulary in the cloud">
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="register-email">Email</label>
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
              {fieldErrors.email === 'This email is already registered.' ? (
                <>
                  (
                  <button
                    className="auth-inline-link"
                    type="button"
                    onClick={() =>
                      navigate(`/login?email=${encodeURIComponent(email.trim())}`)
                    }
                  >
                    Go to Log In
                  </button>
                  )
                </>
              ) : null}
            </p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="register-password">Password</label>
          <PasswordField
            id="register-password"
            autoComplete="new-password"
            placeholder="Set a password (min 8 chars, letters and numbers)"
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
          <label htmlFor="register-confirm">Confirm Password</label>
          <PasswordField
            id="register-confirm"
            autoComplete="new-password"
            placeholder="Re-enter your password"
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
              <Spinner /> Creating…
            </>
          ) : (
            'Create Account'
          )}
        </button>

        {showRetry ? (
          <button
            className="auth-ghost-cta"
            type="button"
            disabled={cooldown.isCooling || isSubmitting}
            onClick={() => void submitRegistration()}
          >
            {cooldown.isCooling ? `Resend available in ${cooldown.remaining}s` : 'Resend'}
          </button>
        ) : null}
      </form>

      <p className="auth-switch">
        Already have an account?
        <button
          className="auth-text-link"
          type="button"
          onClick={() =>
            navigate(
              email.trim() !== '' ? `/login?email=${encodeURIComponent(email.trim())}` : '/login'
            )
          }
        >
          Log In
        </button>
      </p>
    </AuthCard>
  );
}
