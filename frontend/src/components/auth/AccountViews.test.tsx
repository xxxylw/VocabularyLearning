import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LoginView } from './LoginView';
import { RegisterView } from './RegisterView';
import type { AuthUser } from '../../api';

function ok(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const testUser: AuthUser = { id: '1', email: 'user@example.com', emailVerified: true, isSuper: false };

describe('LoginView', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the CTA disabled until the email is valid and a password is entered', async () => {
    const user = userEvent.setup();
    render(<LoginView onLoginSuccess={vi.fn()} />);

    const cta = screen.getByRole('button', { name: 'Log In' });
    expect(cta).toBeDisabled();

    await user.type(screen.getByLabelText('Email'), 'user@example.com');
    expect(cta).toBeDisabled();

    await user.type(screen.getByLabelText('Password'), 'secret123');
    expect(cta).toBeEnabled();
  });

  it('flags an invalid email on blur', async () => {
    const user = userEvent.setup();
    render(<LoginView onLoginSuccess={vi.fn()} />);

    const email = screen.getByLabelText('Email');
    await user.type(email, 'not-an-email');
    fireEvent.blur(email);

    expect(await screen.findByText('Invalid email address')).toBeInTheDocument();
  });

  it('submits the credentials and hands the session to the shell', async () => {
    const user = userEvent.setup();
    const onLoginSuccess = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ token: 'tok-1', user: testUser })
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<LoginView onLoginSuccess={onLoginSuccess} />);
    await user.type(screen.getByLabelText('Email'), 'user@example.com');
    await user.type(screen.getByLabelText('Password'), 'secret123');
    await user.click(screen.getByRole('button', { name: 'Log In' }));

    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalledWith('tok-1', testUser));
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'user@example.com', password: 'secret123' })
    });
  });

  it('shows the not-verified branch with 去查收 / 重发 on 403', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        ok({ detail: { code: 'email_not_verified', message: '该邮箱尚未激活，请查收验证邮件' } }, 403)
      )
      .mockResolvedValueOnce(ok({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    render(<LoginView onLoginSuccess={vi.fn()} />);
    await user.type(screen.getByLabelText('Email'), 'user@example.com');
    await user.type(screen.getByLabelText('Password'), 'secret123');
    await user.click(screen.getByRole('button', { name: 'Log In' }));

    expect(await screen.findByText(/isn't verified yet/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'View Email' }));
    expect(window.location.hash).toBe('#/check-email?email=user%40example.com');

    await user.click(screen.getByRole('button', { name: 'Resend' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/auth/resend-verification', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'user@example.com' })
      })
    );
  });

  it('shows 邮箱或密码不正确 on 401', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(ok({ detail: '邮箱或密码不正确' }, 401))
    );

    render(<LoginView onLoginSuccess={vi.fn()} />);
    await user.type(screen.getByLabelText('Email'), 'user@example.com');
    await user.type(screen.getByLabelText('Password'), 'secret123');
    await user.click(screen.getByRole('button', { name: 'Log In' }));

    expect(await screen.findByText('Incorrect email or password.')).toBeInTheDocument();
  });

  it('toggles the password visibility through the trailing eye button', async () => {
    const user = userEvent.setup();
    render(<LoginView onLoginSuccess={vi.fn()} />);

    const password = screen.getByLabelText('Password');
    await user.type(password, 'secret123');
    expect(password).toHaveAttribute('type', 'password');

    const toggle = screen.getByRole('button', { name: 'Show password' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');

    await user.click(toggle);
    expect(password).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: 'Hide password' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(password).toHaveAttribute('type', 'password');
  });

  it('auto-focuses the password field when arriving from verify/reset', () => {
    render(<LoginView initialEmail="user@example.com" autoFocusPassword onLoginSuccess={vi.fn()} />);

    expect(screen.getByLabelText('Password')).toHaveFocus();
  });

  it('links to forgot-password and register', async () => {
    const user = userEvent.setup();
    render(<LoginView onLoginSuccess={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Forgot your password?' }));
    expect(window.location.hash).toBe('#/forgot-password');

    window.location.hash = '';
    await user.click(screen.getByRole('button', { name: 'Sign Up' }));
    expect(window.location.hash).toBe('#/register');
  });
});

describe('RegisterView', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('enables the CTA only when all three fields are valid', async () => {
    const user = userEvent.setup();
    render(<RegisterView />);

    const cta = screen.getByRole('button', { name: 'Create Account' });
    expect(cta).toBeDisabled();

    await user.type(screen.getByLabelText('Email'), 'new@example.com');
    await user.type(screen.getByLabelText('Password'), 'short');
    expect(cta).toBeDisabled();

    // letters-only misses the new letter+digit policy
    await user.clear(screen.getByLabelText('Password'));
    await user.type(screen.getByLabelText('Password'), 'onlyletterstoolong');
    expect(cta).toBeDisabled();

    await user.clear(screen.getByLabelText('Password'));
    await user.type(screen.getByLabelText('Password'), 'longenough1');
    expect(cta).toBeDisabled();

    await user.type(screen.getByLabelText('Confirm Password'), 'longenough1');
    expect(cta).toBeEnabled();
  });

  it('shows the letter+digit complexity hint as a field error on blur', async () => {
    const user = userEvent.setup();
    render(<RegisterView />);

    // standing hint below the field
    expect(screen.getByText('At least 8 characters, with letters and numbers')).toBeInTheDocument();

    const password = screen.getByLabelText('Password');
    await user.type(password, 'onlyletters');
    fireEvent.blur(password);

    // after blur the inline error repeats the policy copy inside the field
    const field = password.closest('.auth-field');
    expect(field?.querySelector('.auth-inline-error')?.textContent).toBe(
      'At least 8 characters, with letters and numbers'
    );
  });

  it('flags mismatched confirmation on blur', async () => {
    const user = userEvent.setup();
    render(<RegisterView />);

    const confirm = screen.getByLabelText('Confirm Password');
    await user.type(confirm, 'different-password');
    fireEvent.blur(confirm);

    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument();
  });

  it('registers and hands off to /check-email without auto-login', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(ok({ email: 'new@example.com', message: '账号已创建，请查收验证邮件' }, 201));
    vi.stubGlobal('fetch', fetchMock);

    render(<RegisterView />);
    await user.type(screen.getByLabelText('Email'), 'new@example.com');
    await user.type(screen.getByLabelText('Password'), 'longenough1');
    await user.type(screen.getByLabelText('Confirm Password'), 'longenough1');
    await user.click(screen.getByRole('button', { name: 'Create Account' }));

    await waitFor(() =>
      expect(window.location.hash).toBe('#/check-email?email=new%40example.com&from=register')
    );
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'new@example.com', password: 'longenough1' })
    });
  });

  it('shows 该邮箱已注册 with a 去登录 link on 409', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(ok({ detail: { code: 'email_taken', message: '该邮箱已注册' } }, 409))
    );

    render(<RegisterView />);
    await user.type(screen.getByLabelText('Email'), 'taken@example.com');
    await user.type(screen.getByLabelText('Password'), 'longenough1');
    await user.type(screen.getByLabelText('Confirm Password'), 'longenough1');
    await user.click(screen.getByRole('button', { name: 'Create Account' }));

    expect(await screen.findByText('This email is already registered.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Go to Log In' }));
    expect(window.location.hash).toBe('#/login?email=taken%40example.com');
  });

  it('offers a 503 resend retry when the verification email fails to send', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        ok({ detail: { code: 'email_send_failed', message: '验证邮件发送失败，请稍后重试' } }, 503)
      )
      .mockResolvedValueOnce(ok({ email: 'new@example.com', message: 'ok' }, 201));
    vi.stubGlobal('fetch', fetchMock);

    render(<RegisterView />);
    await user.type(screen.getByLabelText('Email'), 'new@example.com');
    await user.type(screen.getByLabelText('Password'), 'longenough1');
    await user.type(screen.getByLabelText('Confirm Password'), 'longenough1');
    await user.click(screen.getByRole('button', { name: 'Create Account' }));

    expect(await screen.findByText('Failed to send the verification email. Please try again later.')).toBeInTheDocument();

    // The backend rolls the half-created account back on 503, so the
    // retry must re-run registration (not a bare resend).
    await user.click(screen.getByRole('button', { name: 'Resend' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'new@example.com', password: 'longenough1' })
      })
    );
  });
});
