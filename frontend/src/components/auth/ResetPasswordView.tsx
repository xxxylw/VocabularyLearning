import { useState } from 'react';
import { ApiError, resetPassword } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  CodeInput,
  PasswordField,
  Spinner,
  Toast,
  isValidEmailFormat,
  isValidPassword,
  useFlash,
  PASSWORD_POLICY_HINT
} from './shared';

type ResetPasswordViewProps = {
  // ?email=… from the forgot-password hop; empty when the page is
  // opened directly — the form then collects the address itself.
  initialEmail?: string;
};

// C-05c + C-01a: the password-reset form. The emailed 6-digit code is
// typed into the segmented input (auto-advance + paste); submitting
// posts {email, code, newPassword} — there is no link token to peek at
// anymore, so the page is fully self-contained.
export function ResetPasswordView({ initialEmail }: ResetPasswordViewProps) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
    confirm?: string;
  }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toastMessage, showToast] = useFlash();

  const emailValid = isValidEmailFormat(email);
  const passwordsMatch = newPassword !== '' && newPassword === confirmPassword;
  const canSubmit =
    emailValid && code.length === 6 && isValidPassword(newPassword) && passwordsMatch && !isSubmitting;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    setFormError(null);
    setIsSubmitting(true);
    try {
      await resetPassword(email.trim(), code, newPassword);
      showToast('Password has been reset.');
      navigate(`/login?from=reset&email=${encodeURIComponent(email.trim())}`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFormError(error.message !== '' ? error.message : 'Failed to reset the password. Please try again later.');
        // A rejected code (wrong / expired / exhausted) clears the boxes
        // so the user retypes; the address and password fields stay.
        setCode('');
      } else {
        setFormError('Network error. Please try again later.');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="Set a New Password"
      subtitle="Enter the 6-digit code from the email and set a new password"
    >
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="reset-email">Email</label>
          <input
            id="reset-email"
            className={fieldErrors.email !== undefined ? 'auth-input has-error' : 'auth-input'}
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            disabled={isSubmitting}
            onChange={(event) => setEmail(event.target.value)}
            onBlur={() =>
              setFieldErrors((prev) => ({
                ...prev,
                email: email !== '' && !emailValid ? 'Invalid email address' : undefined
              }))
            }
          />
          {fieldErrors.email !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.email}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="reset-code-0">Code</label>
          <CodeInput
            idPrefix="reset"
            value={code}
            onChange={(next) => setCode(next)}
            disabled={isSubmitting}
            hasError={formError !== null}
          />
          <p className="auth-field-hint">The 6-digit code from the email; valid for 10 minutes</p>
        </div>

        <div className="auth-field">
          <label htmlFor="reset-new-password">New Password</label>
          <PasswordField
            id="reset-new-password"
            autoComplete="new-password"
            placeholder="Set a password (min 8 chars, letters and numbers)"
            value={newPassword}
            hasError={fieldErrors.password !== undefined}
            disabled={isSubmitting}
            onChange={(event) => setNewPassword(event.target.value)}
            onBlur={() =>
              setFieldErrors((prev) => ({
                ...prev,
                password:
                  newPassword !== '' && !isValidPassword(newPassword)
                    ? PASSWORD_POLICY_HINT
                    : undefined
              }))
            }
          />
          <p className="auth-field-hint">{PASSWORD_POLICY_HINT}</p>
          {fieldErrors.password !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.password}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="reset-confirm-password">Confirm New Password</label>
          <PasswordField
            id="reset-confirm-password"
            autoComplete="new-password"
            placeholder="Re-enter your new password"
            value={confirmPassword}
            hasError={fieldErrors.confirm !== undefined}
            disabled={isSubmitting}
            onChange={(event) => setConfirmPassword(event.target.value)}
            onBlur={() =>
              setFieldErrors((prev) => ({
                ...prev,
                confirm: confirmPassword !== '' && !passwordsMatch ? 'Passwords do not match.' : undefined
              }))
            }
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
              <Spinner /> Resetting…
            </>
          ) : (
            'Reset Password'
          )}
        </button>
      </form>

      <p className="auth-switch">
        Didn't receive the code?
        <button className="auth-text-link" type="button" onClick={() => navigate('/forgot-password')}>
          Get a New Code
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
