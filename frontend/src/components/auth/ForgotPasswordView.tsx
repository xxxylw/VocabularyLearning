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
      showToast('Code sent');
      navigate(`/reset-password?email=${encodeURIComponent(email.trim())}`);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 429) {
          setFormError(error.message !== '' ? error.message : 'Too many requests. Please try again later.');
        } else if (error.status === 503) {
          setFormError('Failed to send the reset email. Please try again later.');
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError('Network error. Please try again later.');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard eyebrow="FUSIO" title="Forgot Password" subtitle="Enter your registered email and we'll send a 6-digit code">
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="forgot-email">Email</label>
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
              setEmailError(email !== '' && !emailValid ? 'Invalid email address' : undefined)
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
              <Spinner /> Sending…
            </>
          ) : (
            'Send Code'
          )}
        </button>
      </form>

      <p className="auth-note">The code is valid for 10 minutes; after 5 failed attempts you'll need a new one.</p>

      <p className="auth-switch">
        Remembered your password?
        <button className="auth-text-link" type="button" onClick={() => navigate('/login')}>
          Back to Log In
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
