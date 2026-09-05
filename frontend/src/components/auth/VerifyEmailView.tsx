import { navigate } from '../../router';
import { AuthCard } from './shared';

// C-01a: the legacy landing page of the old activation emails
// ({base}/#/verify-email?token=…). Link verification is retired — the
// backend answers 410 link_disabled to every GET — so this page only
// explains the switch and routes the user into the code flow. No fetch
// is made (the backend rejects any token anyway).
//
// C-05a spec (designer walkthrough P2 #4): H1 「链接已失效」+ 文案
// 「验证方式已升级为 6 位验证码」+ 主 CTA 「重新获取验证码」跳
// /check-email；去登录 降级为次要文字链。
export function VerifyEmailView({ email }: { email?: string }) {
  return (
    <AuthCard
      eyebrow="VOCABULARYLEARNING"
      title="链接已失效"
      subtitle="验证方式已升级为 6 位验证码"
    >
      <p className="auth-inline-error auth-form-error" role="alert">
        激活链接已停用，请使用邮件中的 6 位数字验证码
      </p>
      <p className="auth-note">最新邮件里直接印有验证码；旧邮件中的链接无法使用</p>
      <button
        className="auth-ghost-cta"
        type="button"
        onClick={() =>
          email !== undefined && email !== ''
            ? navigate(`/check-email?email=${encodeURIComponent(email)}`)
            : navigate('/check-email')
        }
      >
        重新获取验证码
      </button>
      <button
        className="auth-text-link"
        type="button"
        onClick={() => navigate('/login')}
      >
        去登录
      </button>
    </AuthCard>
  );
}
