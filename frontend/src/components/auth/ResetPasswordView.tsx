import { useEffect, useState } from 'react';
import { ApiError, fetchResetTokenInfo, resetPassword } from '../../api';
import { navigate } from '../../router';
import {
  AuthCard,
  Spinner,
  Toast,
  isValidPassword,
  useFlash
} from './shared';

type ResetPasswordViewProps = {
  token: string;
};

type TokenState =
  | { status: 'checking' }
  | { status: 'valid'; email: string }
  | { status: 'invalid' };

// C-05c: the landing page of the reset link from the email. The token
// lives in the URL hash query (?token=…). We peek at it via
// GET /api/auth/reset-token-info so the form can show which address the
// new password is for; a 410 means expired/used → 重新申请 branch.
export function ResetPasswordView({ token }: ResetPasswordViewProps) {
  const [tokenState, setTokenState] = useState<TokenState>({ status: 'checking' });
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ password?: string; confirm?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toastMessage, showToast] = useFlash();

  useEffect(() => {
    let cancelled = false;
    fetchResetTokenInfo(token)
      .then((info) => {
        if (!cancelled) {
          setTokenState({ status: 'valid', email: info.email });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTokenState({ status: 'invalid' });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const passwordsMatch = newPassword !== '' && newPassword === confirmPassword;
  const canSubmit =
    tokenState.status === 'valid' &&
    isValidPassword(newPassword) &&
    passwordsMatch &&
    !isSubmitting;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    setFormError(null);
    setIsSubmitting(true);
    try {
      await resetPassword(token, newPassword);
      showToast('密码已重置');
      const email = tokenState.status === 'valid' ? tokenState.email : '';
      navigate(`/login?from=reset&email=${encodeURIComponent(email)}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 410) {
        setTokenState({ status: 'invalid' });
      } else if (error instanceof ApiError && error.status === 429) {
        setFormError(error.message !== '' ? error.message : '发送过于频繁，请稍后再试');
      } else {
        setFormError('网络异常，请稍后重试');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (tokenState.status === 'checking') {
    return (
      <AuthCard eyebrow="VOCABULARYLEARNING" title="设置新密码">
        <p className="auth-subtitle">
          <Spinner /> 正在校验链接…
        </p>
      </AuthCard>
    );
  }

  if (tokenState.status === 'invalid') {
    return (
      <AuthCard eyebrow="VOCABULARYLEARNING" title="设置新密码" subtitle="这个重置链接已失效或已使用">
        <p className="auth-inline-error auth-form-error" role="alert">
          链接已失效或已使用
        </p>
        <button
          className="auth-ghost-cta"
          type="button"
          onClick={() => navigate('/forgot-password')}
        >
          重新申请
        </button>
        <button className="auth-text-link" type="button" onClick={() => navigate('/login')}>
          回登录
        </button>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="设置新密码"
      subtitle={
        <>
          为 <strong className="auth-email-echo">{tokenState.email}</strong> 设置新密码
        </>
      }
    >
      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <div className="auth-field">
          <label htmlFor="reset-new-password">新密码</label>
          <input
            id="reset-new-password"
            className={fieldErrors.password !== undefined ? 'auth-input has-error' : 'auth-input'}
            type="password"
            autoComplete="new-password"
            placeholder="设置新密码（至少 8 位）"
            value={newPassword}
            disabled={isSubmitting}
            onChange={(event) => setNewPassword(event.target.value)}
            onBlur={() =>
              setFieldErrors((prev) => ({
                ...prev,
                password: newPassword !== '' && !isValidPassword(newPassword) ? '密码至少 8 位' : undefined
              }))
            }
          />
          <p className="auth-field-hint">至少 8 位</p>
          {fieldErrors.password !== undefined ? (
            <p className="auth-inline-error">{fieldErrors.password}</p>
          ) : null}
        </div>

        <div className="auth-field">
          <label htmlFor="reset-confirm-password">确认新密码</label>
          <input
            id="reset-confirm-password"
            className={fieldErrors.confirm !== undefined ? 'auth-input has-error' : 'auth-input'}
            type="password"
            autoComplete="new-password"
            placeholder="再次输入新密码"
            value={confirmPassword}
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
        <button className="auth-text-link" type="button" onClick={() => navigate('/login')}>
          回登录
        </button>
      </p>
      <Toast message={toastMessage} />
    </AuthCard>
  );
}
