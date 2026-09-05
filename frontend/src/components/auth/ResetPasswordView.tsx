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
      showToast('密码已重置');
      navigate(`/login?from=reset&email=${encodeURIComponent(email.trim())}`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFormError(error.message !== '' ? error.message : '重置失败，请稍后重试');
        // A rejected code (wrong / expired / exhausted) clears the boxes
        // so the user retypes; the address and password fields stay.
        setCode('');
      } else {
        setFormError('网络异常，请稍后重试');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="设置新密码"
      subtitle="输入邮件中的 6 位数字验证码，并设置新密码"
    >
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="reset-email">邮箱</label>
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
                email: email !== '' && !emailValid ? '邮箱格式不正确' : undefined
              }))
            }
          />
          {fieldErrors.email !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.email}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="reset-code-0">验证码</label>
          <CodeInput
            idPrefix="reset"
            value={code}
            onChange={(next) => setCode(next)}
            disabled={isSubmitting}
            hasError={formError !== null}
          />
          <p className="auth-field-hint">邮件中的 6 位数字验证码，10 分钟内有效</p>
        </div>

        <div className="auth-field">
          <label htmlFor="reset-new-password">新密码</label>
          <PasswordField
            id="reset-new-password"
            autoComplete="new-password"
            placeholder="设置新密码（至少 8 位，含字母和数字）"
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
          <label htmlFor="reset-confirm-password">确认新密码</label>
          <PasswordField
            id="reset-confirm-password"
            autoComplete="new-password"
            placeholder="再次输入新密码"
            value={confirmPassword}
            hasError={fieldErrors.confirm !== undefined}
            disabled={isSubmitting}
            onChange={(event) => setConfirmPassword(event.target.value)}
            onBlur={() =>
              setFieldErrors((prev) => ({
                ...prev,
                confirm: confirmPassword !== '' && !passwordsMatch ? '两次输入的密码不一致' : undefined
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
              <Spinner /> 重置中…
            </>
          ) : (
            '重置密码'
          )}
        </button>
      </form>

      <p className="auth-switch">
        没收到验证码？
        <button className="auth-text-link" type="button" onClick={() => navigate('/forgot-password')}>
          重新获取
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
