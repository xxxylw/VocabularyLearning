// v2 cloud auth: the session token handed out by POST /api/auth/login.
// Stored under a namespaced key so future cloud features can't collide
// with the v1.1 check-in storage. The token is the only auth artifact
// kept client-side — passwords never touch localStorage.

const TOKEN_STORAGE_KEY = 'vocabularylearning.session.token';

export function getSessionToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    // localStorage can be unavailable (privacy mode / disabled storage)
    // — degrade to a session-less client rather than crashing the app.
    return null;
  }
}

export function setSessionToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // Best-effort persistence; the in-memory auth state still works
    // for this tab until reload.
  }
}

export function clearSessionToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}
