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

// C-01 / batch-2 D3: the backend policy (auth.password_policy_error)
// now requires letters AND digits on every password-setting path; this
// mirror keeps the client-side gate and the inline hint in sync with it.
export const PASSWORD_POLICY_HINT = '至少 8 位，且需包含字母和数字';

export function isValidPassword(password: string): boolean {
  return (
    password.length >= PASSWORD_MIN_LENGTH && /[a-zA-Z]/.test(password) && /\d/.test(password)
  );
}

// Shared password input with the C-01 trailing show/hide toggle: the
// eye icon is 28×28 visually but sits on a 44×44 touch hot area (padding
// 8px + background-clip: content-box, see auth.css), and visibility is
// toggled by switching the input's type between password / text.
export function PasswordField({
  id,
  autoComplete,
  placeholder,
  value,
  hasError,
  disabled,
  autoFocus,
  onChange,
  onBlur
}: {
  id: string;
  autoComplete: string;
  placeholder: string;
  value: string;
  hasError?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onBlur?: () => void;
}) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="auth-password-wrap">
      <input
        id={id}
        className={hasError ? 'auth-input has-error auth-password-input' : 'auth-input auth-password-input'}
        type={visible ? 'text' : 'password'}
        autoComplete={autoComplete}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        autoFocus={autoFocus}
        onChange={onChange}
        onBlur={onBlur}
      />
      <button
        className="auth-password-toggle"
        type="button"
        aria-label={visible ? '隐藏密码' : '显示密码'}
        aria-pressed={visible}
        disabled={disabled}
        onClick={() => setVisible((current) => !current)}
      >
        {visible ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg
      width="28"
      height="28"
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M4 14s3.8-6.4 10-6.4S24 14 24 14s-3.8 6.4-10 6.4S4 14 4 14Z"
        stroke="#486f83"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <circle cx="14" cy="14" r="2.9" stroke="#486f83" strokeWidth="2" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg
      width="28"
      height="28"
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M5 5l18 18"
        stroke="#486f83"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M10.3 8.1A10.9 10.9 0 0 1 14 7.6c6.2 0 10 6.4 10 6.4a17.9 17.9 0 0 1-3.1 3.7M17 19.9a10.6 10.6 0 0 1-3 .5c-6.2 0-10-6.4-10-6.4a17.7 17.7 0 0 1 3.2-3.8"
        stroke="#486f83"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M11.5 11.7a3.4 3.4 0 0 0 4.8 4.9"
        stroke="#486f83"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

// C-05 spec (retired by C-01a): the "打开邮箱" CTA was removed together
// with the mailto: hand-off — the check-email page now hosts the 6-digit
// code input itself, so there is nothing to hand off to.

// ---------------------------------------------------------------------------
// C-01a: the 6-digit email verification code input.
//
// Six single-character boxes with auto-advance, paste support (any
// pasted text keeps only its digits), and keyboard navigation. The
// component is fully controlled via a contiguous digit string — box i
// shows value[i], the parent owns submission.
// ---------------------------------------------------------------------------

export const EMAIL_CODE_LENGTH = 6;

export function CodeInput({
  idPrefix,
  value,
  onChange,
  disabled,
  hasError
}: {
  idPrefix: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  hasError?: boolean;
}) {
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  function focusBox(index: number) {
    refs.current[Math.min(Math.max(index, 0), EMAIL_CODE_LENGTH - 1)]?.focus();
  }

  function handleBoxChange(index: number, raw: string) {
    const cleaned = raw.replace(/\D/g, '');
    if (cleaned === '') {
      // The box was cleared (Backspace/Delete on its own digit): drop
      // that digit, keeping the remaining ones contiguous.
      onChange(value.slice(0, index) + value.slice(index + 1));
      return;
    }
    const digit = cleaned.slice(-1);
    if (index < value.length) {
      // Overwrite the digit that already sits in this box.
      onChange(value.slice(0, index) + digit + value.slice(index + 1));
      focusBox(index + 1);
    } else {
      // Typing ahead of the filled prefix appends at the end.
      const next = (value + digit).slice(0, EMAIL_CODE_LENGTH);
      onChange(next);
      focusBox(next.length);
    }
  }

  function handleKeyDown(index: number, event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Backspace' && value[index] === undefined) {
      // Backspace on an empty box clears the previous one and moves
      // the caret there, the way OTP inputs behave.
      event.preventDefault();
      if (index > 0) {
        onChange(value.slice(0, index - 1));
        focusBox(index - 1);
      }
    }
    if (event.key === 'ArrowLeft' && index > 0) {
      event.preventDefault();
      focusBox(index - 1);
    }
    if (event.key === 'ArrowRight' && index < EMAIL_CODE_LENGTH - 1) {
      event.preventDefault();
      focusBox(index + 1);
    }
  }

  function handlePaste(event: React.ClipboardEvent<HTMLInputElement>) {
    event.preventDefault();
    const text = event.clipboardData.getData('text');
    const digits = text.replace(/\D/g, '').slice(0, EMAIL_CODE_LENGTH);
    if (digits === '') {
      return;
    }
    onChange(digits);
    focusBox(digits.length);
  }

  return (
    <div className={hasError ? 'auth-code-input has-error' : 'auth-code-input'}>
      {Array.from({ length: EMAIL_CODE_LENGTH }, (_, index) => (
        <input
          key={`${idPrefix}-code-${index}`}
          ref={(element) => {
            refs.current[index] = element;
          }}
          id={`${idPrefix}-code-${index}`}
          className="auth-code-box"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={1}
          value={value[index] ?? ''}
          disabled={disabled}
          autoFocus={index === 0}
          aria-label={`验证码第 ${index + 1} 位`}
          onChange={(event) => handleBoxChange(index, event.target.value)}
          onKeyDown={(event) => handleKeyDown(index, event)}
          onPaste={handlePaste}
          onFocus={(event) => event.target.select()}
        />
      ))}
    </div>
  );
}
