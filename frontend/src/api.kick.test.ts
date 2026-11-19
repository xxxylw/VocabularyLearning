import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, fetchCurrentUser, getCurrentBook, reviewCard } from './api';
import { getSessionToken, setSessionToken } from './session';

// 单点登录（2026-11-11 需求一）：账号在别处登录后，本端任何 API 的 401
// + code=session_superseded 必须 ① 清掉本地 token ② 整页跳到
// /login?kick=1（登录页展示 "You signed in on another device."）。
// 三个请求助手（authJson / getJson / postJson）统一覆盖；普通 401
// （错误密码、过期 token）不得触发跳转。

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: () => Promise.resolve(JSON.stringify(body))
  };
}

const KICKED_BODY = {
  detail: {
    code: 'session_superseded',
    message: 'You signed in on another device. Please sign in again.'
  }
};

describe('kicked session handling (single-session sign-in)', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.location.hash = '';
  });

  it('clears the token and redirects to /login?kick=1 on a superseded 401 (authJson)', async () => {
    setSessionToken('old-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(KICKED_BODY, 401)));

    const error = await fetchCurrentUser().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBe('session_superseded');
    expect(getSessionToken()).toBeNull();
    expect(window.location.hash).toBe('#/login?kick=1');
  });

  it('handles a superseded 401 in the study GET helper too', async () => {
    setSessionToken('old-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(KICKED_BODY, 401)));

    const error = await getCurrentBook().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe('session_superseded');
    expect(getSessionToken()).toBeNull();
    expect(window.location.hash).toBe('#/login?kick=1');
  });

  it('handles a superseded 401 in the study POST helper too', async () => {
    setSessionToken('old-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(KICKED_BODY, 401)));

    const error = await reviewCard('card-1', 'known').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe('session_superseded');
    expect(getSessionToken()).toBeNull();
    expect(window.location.hash).toBe('#/login?kick=1');
  });

  it('does NOT redirect on a plain 401 (wrong password / expired token)', async () => {
    setSessionToken('old-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'Invalid or expired session' }, 401))
    );

    const error = await fetchCurrentUser().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBeUndefined();
    // 非顶号 401：现有行为保持不变（不跳页、不在 api 层清 token）。
    expect(getSessionToken()).toBe('old-token');
    expect(window.location.hash).toBe('');
  });
});
