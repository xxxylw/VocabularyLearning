import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

// Shared building blocks for the v2 cloud auth pages (C-01/C-02/C-05).
// All visual tokens come from the UI spec's section-1 baseline and are
// implemented in auth.css — nothing here invents new colors or sizes.

export function AuthCard({
  eyebrow,
  title,
  subtitle,
  icon,
  children
}: {
  eyebrow?: string;
  title: string;
  subtitle?: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        {eyebrow !== undefined && eyebrow !== '' ? <p className="eyebrow">{eyebrow}</p> : null}
        {icon}
        <h1 className="auth-title">{title}</h1>
        {subtitle !== undefined && subtitle !== null ? (
          <p className="auth-subtitle">{subtitle}</p>
        ) : null}
        {children}
      </section>
    </main>
  );
}

export function EnvelopeIcon() {
  return (
    <svg
      className="auth-envelope"
      width="56"
      height="56"
      viewBox="0 0 56 56"
      fill="none"
      aria-hidden="true"
    >
      <rect x="8" y="14" width="40" height="28" rx="4" stroke="#486f83" strokeWidth="2" />
      <path
        d="M10 18.5L28 31.5L46 18.5"
        stroke="#486f83"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Spinner() {
  return <span className="auth-spinner" aria-hidden="true" />;
}

export function Toast({ message }: { message: string | null }) {
  if (message === null) {
    return null;
  }
  return (
    <div className="auth-toast" role="status" aria-live="polite">
      {message}
    </div>
  );
}

export function useFlash(durationMs = 3000): [string | null, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const show = useCallback(
    (next: string) => {
      setMessage(next);
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => setMessage(null), durationMs);
    },
    [durationMs]
  );

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    []
  );

  return [message, show];
}

// Shared 60-second resend cooldown (spec C-05: same cooldown object for
// register / check-email / forgot-password resend actions).
export function useCooldown() {
  const [remaining, setRemaining] = useState(0);
  const isCooling = remaining > 0;

  // A single interval (not chained per-second timeouts) so the countdown
  // keeps ticking even when React commits are batched/deferred — e.g.
  // under fake timers in tests or busy main threads.
  useEffect(() => {
    if (!isCooling) {
      return;
    }
    const timer = window.setInterval(() => {
      setRemaining((value) => (value > 0 ? value - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isCooling]);

  const start = useCallback((seconds: number) => {
    setRemaining(seconds);
  }, []);

  return { remaining, isCooling, start };
}

export function isValidEmailFormat(email: string): boolean {
  const trimmed = email.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
}

export const PASSWORD_MIN_LENGTH = 8;

export function isValidPassword(password: string): boolean {
  return password.length >= PASSWORD_MIN_LENGTH;
}

// C-05 spec: the "打开邮箱" CTA hands off to the OS default mail client
// without embedding a specific provider.
export function openMailClient(): void {
  try {
    window.location.href = 'mailto:';
  } catch {
    // jsdom / exotic environments — the surrounding UI still tells the
    // user to open their mailbox manually.
  }
}
