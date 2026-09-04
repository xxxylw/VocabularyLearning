import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  fetchCurrentUser,
  getCurrentBook,
  login,
  logout,
  register,
  resetPassword,
  verifyEmail
} from './api';
import { setSessionToken } from './session';

// v2 cloud auth API contract tests. The exact-fetch-shape guarantees
// for the anonymous case (no Authorization header, single-arg GET) are
// guarded by api.test.ts — here we cover the token-injected shapes and
// the structured ApiError mapping the views branch on.

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

describe('auth api', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('logs in with email and password and no token header when anonymous', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        token: 'session-token-1',
        user: { id: '1', email: 'a@example.com', emailVerified: true, isSuper: false }
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await login('a@example.com', 'secret123');

    expect(result.token).toBe('session-token-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'a@example.com', password: 'secret123' })
    });
  });

  it('attaches the Authorization header when a session token is stored', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: '1', email: 'a@example.com', emailVerified: true, isSuper: false })
    );
    vi.stubGlobal('fetch', fetchMock);

    await fetchCurrentUser();

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', {
      method: 'GET',
      headers: { Authorization: 'Bearer stored-token' }
    });
  });

  it('injects the Authorization header into the v1.1 study GET helper too', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ totalWords: 0, nextSequenceIndex: null })
    );
    vi.stubGlobal('fetch', fetchMock);

    await getCurrentBook();

    expect(fetchMock).toHaveBeenCalledWith('/api/books/current', {
      headers: { Authorization: 'Bearer stored-token' }
    });
  });

  it('sends logout with the stored token', async () => {
    setSessionToken('stored-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await logout();

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/logout', {
      method: 'POST',
      headers: { Authorization: 'Bearer stored-token' }
    });
  });

  it('maps structured error details to ApiError with status and code', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: { code: 'email_not_verified', message: '该邮箱尚未激活，请查收验证邮件' } },
        403
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await login('a@example.com', 'secret123').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    expect((error as ApiError).code).toBe('email_not_verified');
    expect((error as ApiError).message).toBe('该邮箱尚未激活，请查收验证邮件');
  });

  it('maps a plain-string detail (401) to ApiError without a code', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: '邮箱或密码不正确' }, 401)
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await login('a@example.com', 'wrong').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBeUndefined();
    expect((error as ApiError).message).toBe('邮箱或密码不正确');
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      text: () => Promise.resolve('<html>proxy error</html>')
    });
    vi.stubGlobal('fetch', fetchMock);

    const error = await register('a@example.com', 'secret123').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).message).toContain('502');
  });

  it('verifies an email token via GET with an encoded query', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ email: 'a@example.com' }));
    vi.stubGlobal('fetch', fetchMock);

    await verifyEmail('abc/def+ghi');

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/verify-email?token=abc%2Fdef%2Bghi', {
      method: 'GET',
      headers: {}
    });
  });

  it('resets a password with token and newPassword in the body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await resetPassword('tok-1', 'new-secret-1');

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: 'tok-1', newPassword: 'new-secret-1' })
    });
  });
});
