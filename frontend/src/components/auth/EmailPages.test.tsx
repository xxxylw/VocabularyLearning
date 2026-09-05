import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckEmailView } from './CheckEmailView';
import { ForgotPasswordView } from './ForgotPasswordView';
import { ResetPasswordView } from './ResetPasswordView';
import { VerifyEmailView } from './VerifyEmailView';

function ok(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

describe('CheckEmailView (C-05a)', () => {
  beforeEach(() => {
    window.location.hash = '';
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('echoes the email from the route and starts the 60s cooldown', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" />);

    expect(screen.getByText('查收你的验证邮件')).toBeInTheDocument();
    expect(screen.getByText('new@example.com')).toBeInTheDocument();
    expect(screen.getByText('60s 后可重新发送')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '没收到？重发邮件' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '打开邮箱' })).toBeEnabled();
  });

  it('shows the from=register success banner on the landing page', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" notice="账号已创建，去邮箱激活" />);

    expect(screen.getByText('账号已创建，去邮箱激活')).toBeInTheDocument();
  });

  it('returns to /register with the email prefilled from 修改邮箱', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" />);

    fireEvent.click(screen.getByRole('button', { name: '修改邮箱' }));
    expect(window.location.hash).toBe('#/register?email=new%40example.com');
  });

  it('sends a resend after the cooldown elapses and restarts it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(await screen.findByText('可以重新发送了')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '没收到？重发邮件' }));
    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/auth/resend-verification', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'new@example.com' })
      })
    );
    await vi.waitFor(() => expect(screen.getByText('已重新发送')).toBeInTheDocument());
  });

  it('flips to the 已激活 badge when the status poll reports verified', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ verified: true }));
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    await vi.advanceTimersByTimeAsync(5_100);
    expect(await screen.findByText('已激活')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去登录' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: '去登录' }));
    expect(window.location.hash).toBe('#/login?from=verify&email=new%40example.com');
  });
});

describe('ForgotPasswordView (C-05b)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requests a reset email and switches to the sent state', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ForgotPasswordView />);
    await user.type(screen.getByLabelText('邮箱'), 'user@example.com');
    await user.click(screen.getByRole('button', { name: '发送重置邮件' }));

    expect(await screen.findByText('查收你的重置邮件')).toBeInTheDocument();
    expect(screen.getByText('user@example.com')).toBeInTheDocument();
    expect(screen.getByText('链接 1 小时内有效，仅可使用一次')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'user@example.com' })
    });
  });

  it('links back to login from the sent state', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ ok: true })));

    render(<ForgotPasswordView />);
    await user.type(screen.getByLabelText('邮箱'), 'user@example.com');
    await user.click(screen.getByRole('button', { name: '发送重置邮件' }));
    await screen.findByText('查收你的重置邮件');

    await user.click(screen.getByRole('button', { name: '回登录' }));
    expect(window.location.hash).toBe('#/login');
  });
});

describe('ResetPasswordView (C-05c)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows which account the token belongs to and resets the password', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok({ email: 'user@example.com' }))
      .mockResolvedValueOnce(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ResetPasswordView token="tok-1" />);
    expect(await screen.findByText('user@example.com')).toBeInTheDocument();

    await user.type(screen.getByLabelText('新密码'), 'brand-new-pass1');
    await user.type(screen.getByLabelText('确认新密码'), 'brand-new-pass1');
    await user.click(screen.getByRole('button', { name: '重置密码' }));

    await waitFor(() =>
      expect(window.location.hash).toBe('#/login?from=reset&email=user%40example.com')
    );
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: 'tok-1', newPassword: 'brand-new-pass1' })
    });
  });

  it('falls into the 链接已失效 branch when the token is rejected (410)', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        ok({ detail: { code: 'token_invalid', message: '链接已失效或已使用' } }, 410)
      )
    );

    render(<ResetPasswordView token="expired" />);
    expect(await screen.findByText('链接已失效或已使用')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重新申请' }));
    expect(window.location.hash).toBe('#/forgot-password');
  });
});

describe('VerifyEmailView', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('consumes the token and bounces to /login with the email prefilled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn().mockResolvedValue(ok({ email: 'user@example.com' }));
    vi.stubGlobal('fetch', fetchMock);

    render(<VerifyEmailView token="tok-1" />);
    expect(await screen.findByText('邮箱已激活')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/verify-email?token=tok-1', {
      method: 'GET',
      headers: {}
    });

    await vi.advanceTimersByTimeAsync(1_300);
    expect(window.location.hash).toBe('#/login?from=verify&email=user%40example.com');
    vi.useRealTimers();
  });

  it('shows the invalid-link state on 410', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        ok({ detail: { code: 'token_invalid', message: '链接已失效或已使用' } }, 410)
      )
    );

    render(<VerifyEmailView token="stale" />);
    expect(await screen.findByText('链接已失效或已使用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去登录' })).toBeEnabled();
  });
});
