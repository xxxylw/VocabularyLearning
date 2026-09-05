import { navigate } from '../../router';
import { AuthCard } from './shared';

// C-01a: the legacy landing page of the old activation emails
// ({base}/#/verify-email?token=…). Link verification is retired — the
// backend answers 410 link_disabled to every GET — so this page only
// explains the switch and routes the user into the code flow. No fetch
// is made (the backend rejects any token anyway).
export function VerifyEmailView({ email }: { email?: string }) {
  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="邮箱激活"
      subtitle="链接式验证已停用"
    >
      <p className="auth-inline-error auth-form-error" role="alert">
        激活链接已停用，请使用邮件中的 6 位数字验证码
      </p>
      <p className="auth-note">最新邮件里直接印有验证码；旧邮件中的链接无法使用</p>
      {email !== undefined && email !== '' ? (
        <button
          className="auth-ghost-cta"
          type="button"
          onClick={() => navigate(`/check-email?email=${encodeURIComponent(email)}`)}
        >
          去输入验证码
        </button>
      ) : null}
      <button
        className={email !== undefined && email !== '' ? 'auth-text-link' : 'auth-ghost-cta'}
        type="button"
        onClick={() => navigate('/login')}
      >
        去登录
      </button>
    </AuthCard>
  );
}
