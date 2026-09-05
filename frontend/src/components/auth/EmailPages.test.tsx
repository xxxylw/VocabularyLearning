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

function pasteEvent(text: string) {
  return {
    clipboardData: {
      getData: () => text
    }
  };
}

describe('CheckEmailView (C-05a + C-01a code flow)', () => {
  beforeEach(() => {
    window.location.hash = '';
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('echoes the email, renders six code boxes, and starts the 60s cooldown', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" />);

    expect(screen.getByText('查收你的验证邮件')).toBeInTheDocument();
    expect(screen.getByText('new@example.com')).toBeInTheDocument();
    expect(screen.getByText('60s 后可重新发送')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '没收到？重发验证码' })).toBeDisabled();
    for (let index = 1; index <= 6; index += 1) {
      expect(screen.getByLabelText(`验证码第 ${index} 位`)).toBeInTheDocument();
    }
    // C-01a: the 打开邮箱 hand-off is gone from the check-email page.
    expect(screen.queryByRole('button', { name: '打开邮箱' })).not.toBeInTheDocument();
    expect(
      screen.getByText('验证码 10 分钟内有效；错误 5 次后需重新获取；没收到请检查垃圾邮件箱')
    ).toBeInTheDocument();
  });

  it('shows the from=register success banner on the landing page', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" notice="账号已创建，验证码已发送至你的邮箱" />);

    expect(screen.getByText('账号已创建，验证码已发送至你的邮箱')).toBeInTheDocument();
  });

  it('returns to /register with the email prefilled from 修改邮箱', () => {
    vi.stubGlobal('fetch', vi.fn());
    render(<CheckEmailView email="new@example.com" />);

    fireEvent.click(screen.getByRole('button', { name: '修改邮箱' }));
    expect(window.location.hash).toBe('#/register?email=new%40example.com');
  });

  it('auto-submits the code once all six digits are typed', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ email: 'new@example.com' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('验证码第 1 位'), '1');
    await user.type(screen.getByLabelText('验证码第 2 位'), '2');
    await user.type(screen.getByLabelText('验证码第 3 位'), '3');
    await user.type(screen.getByLabelText('验证码第 4 位'), '4');
    await user.type(screen.getByLabelText('验证码第 5 位'), '5');
    await user.type(screen.getByLabelText('验证码第 6 位'), '6');

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/auth/verify-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'new@example.com', code: '123456' })
      })
    );
    expect(await screen.findByText('已激活')).toBeInTheDocument();
  });

  it('supports pasting a code (digits extracted) into the boxes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ email: 'new@example.com' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    fireEvent.paste(screen.getByLabelText('验证码第 1 位'), pasteEvent('验证码：654321'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/auth/verify-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'new@example.com', code: '654321' })
      })
    );
  });

  it('shows the backend error message and clears the boxes on a rejected code', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ detail: { code: 'code_invalid', message: '验证码错误，还可尝试 4 次' } }, 400)
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('验证码第 1 位'), '9');
    await user.type(screen.getByLabelText('验证码第 2 位'), '9');
    await user.type(screen.getByLabelText('验证码第 3 位'), '9');
    await user.type(screen.getByLabelText('验证码第 4 位'), '9');
    await user.type(screen.getByLabelText('验证码第 5 位'), '9');
    await user.type(screen.getByLabelText('验证码第 6 位'), '9');

    expect(await screen.findByText('验证码错误，还可尝试 4 次')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText('验证码第 1 位')).toHaveValue('');
      expect(screen.getByLabelText('验证码第 6 位')).toHaveValue('');
    });
    // Designer walkthrough C-01a (P2 #1): after a rejected submission
    // the focus must return to the first box for immediate retyping.
    expect(screen.getByLabelText('验证码第 1 位')).toHaveFocus();
  });

  it('sends a resend after the cooldown elapses and restarts it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(await screen.findByText('可以重新发送了')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '没收到？重发验证码' }));
    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/auth/resend-verification', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'new@example.com' })
      })
    );
    await vi.waitFor(() =>
      expect(screen.getByText('新验证码已发送，旧验证码已失效')).toBeInTheDocument()
    );
  });

  it('ignores a double click on resend while a resend is in flight (QA C-01a)', async () => {
    // In-flight guard: a slow resend request + a double click must
    // still produce exactly ONE /api/auth/resend-verification call.
    const fetchMock = vi.fn().mockImplementation(
      (url: string) =>
        url === '/api/auth/resend-verification'
          ? new Promise((resolve) => {
              setTimeout(() => resolve(ok({ ok: true })), 50);
            })
          : Promise.resolve(ok({ verified: false }))
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<CheckEmailView email="new@example.com" />);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(await screen.findByText('可以重新发送了')).toBeInTheDocument();

    const button = screen.getByRole('button', { name: '没收到？重发验证码' });
    fireEvent.click(button);
    // The guard must disable the button while the request is in flight.
    expect(button).toBeDisabled();
    fireEvent.click(button); // double click — must be swallowed

    await vi.advanceTimersByTimeAsync(100);
    await vi.waitFor(() =>
      expect(screen.getByText('新验证码已发送，旧验证码已失效')).toBeInTheDocument()
    );
    const resendCalls = fetchMock.mock.calls.filter(
      ([url]) => url === '/api/auth/resend-verification'
    );
    expect(resendCalls).toHaveLength(1);
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

describe('ForgotPasswordView (C-05b + C-01a code flow)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requests a code and hops to /reset-password with the email prefilled', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ForgotPasswordView />);
    await user.type(screen.getByLabelText('邮箱'), 'user@example.com');
    await user.click(screen.getByRole('button', { name: '发送验证码' }));

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'user@example.com' })
    });
    await waitFor(() =>
      expect(window.location.hash).toBe('#/reset-password?email=user%40example.com')
    );
    // No intermediate sent-state page, no 打开邮箱 hand-off.
    expect(screen.queryByText('查收你的重置邮件')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开邮箱' })).not.toBeInTheDocument();
  });

  it('links back to login', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ ok: true })));

    render(<ForgotPasswordView />);
    await user.type(screen.getByLabelText('邮箱'), 'user@example.com');
    await user.click(screen.getByRole('button', { name: '发送验证码' }));

    await user.click(screen.getByRole('button', { name: '回登录' }));
    expect(window.location.hash).toBe('#/login');
  });
});

describe('ResetPasswordView (C-05c + C-01a code flow)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('resets the password with email + code + newPassword', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ResetPasswordView initialEmail="user@example.com" />);
    expect(screen.getByDisplayValue('user@example.com')).toBeInTheDocument();

    const user2 = userEvent.setup();
    await user2.type(screen.getByLabelText('验证码第 1 位'), '2');
    await user2.type(screen.getByLabelText('验证码第 2 位'), '4');
    await user2.type(screen.getByLabelText('验证码第 3 位'), '6');
    await user2.type(screen.getByLabelText('验证码第 4 位'), '8');
    await user2.type(screen.getByLabelText('验证码第 5 位'), '1');
    await user2.type(screen.getByLabelText('验证码第 6 位'), '0');
    await user2.type(screen.getByLabelText('新密码'), 'brand-new-pass1');
    await user2.type(screen.getByLabelText('确认新密码'), 'brand-new-pass1');
    await user2.click(screen.getByRole('button', { name: '重置密码' }));

    await waitFor(() =>
      expect(window.location.hash).toBe('#/login?from=reset&email=user%40example.com')
    );
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'user@example.com',
        code: '246810',
        newPassword: 'brand-new-pass1'
      })
    });
  });

  it('shows the backend error and clears the code when the code is rejected', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ detail: { code: 'code_max_attempts', message: '验证码错误次数过多已作废，请重新获取' } }, 410)
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<ResetPasswordView initialEmail="user@example.com" />);
    await user.type(screen.getByLabelText('验证码第 1 位'), '1');
    await user.type(screen.getByLabelText('验证码第 2 位'), '1');
    await user.type(screen.getByLabelText('验证码第 3 位'), '1');
    await user.type(screen.getByLabelText('验证码第 4 位'), '1');
    await user.type(screen.getByLabelText('验证码第 5 位'), '1');
    await user.type(screen.getByLabelText('验证码第 6 位'), '1');
    await user.type(screen.getByLabelText('新密码'), 'brand-new-pass1');
    await user.type(screen.getByLabelText('确认新密码'), 'brand-new-pass1');
    await user.click(screen.getByRole('button', { name: '重置密码' }));

    expect(await screen.findByText('验证码错误次数过多已作废，请重新获取')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText('验证码第 1 位')).toHaveValue('');
    });

    await user.click(screen.getByRole('button', { name: '重新获取' }));
    expect(window.location.hash).toBe('#/forgot-password');
  });
});

describe('VerifyEmailView (C-01a legacy links)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the link-disabled notice without any network call', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<VerifyEmailView />);

    expect(screen.getByText('链接已失效')).toBeInTheDocument();
    expect(screen.getByText('验证方式已升级为 6 位验证码')).toBeInTheDocument();
    expect(
      screen.getByText('激活链接已停用，请使用邮件中的 6 位数字验证码')
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新获取验证码' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '去登录' })).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('routes to the code entry page when the email is known', () => {
    vi.stubGlobal('fetch', vi.fn());

    render(<VerifyEmailView email="user@example.com" />);

    fireEvent.click(screen.getByRole('button', { name: '重新获取验证码' }));
    expect(window.location.hash).toBe('#/check-email?email=user%40example.com');
  });

  it('routes to the code entry page without an email too', () => {
    vi.stubGlobal('fetch', vi.fn());

    render(<VerifyEmailView />);

    fireEvent.click(screen.getByRole('button', { name: '重新获取验证码' }));
    expect(window.location.hash).toBe('#/check-email');
  });
});
