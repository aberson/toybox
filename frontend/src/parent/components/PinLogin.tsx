import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  extractPinInvalidDetail,
  extractPinLockedDetail,
  isAbortError,
} from "../api";
import type { ApiClient, ParentTokenResponse } from "../api";

// Step 21: PIN login. Recurring screen shown when a PIN is already
// set. Submitting calls ``POST /api/auth/parent`` with ``{pin}``;
// errors fall through ``extractPinInvalidDetail`` (401, "Wrong PIN. N
// attempts remaining") and ``extractPinLockedDetail`` (423, locked
// state with countdown). The countdown ticks once per second via
// ``setInterval`` and re-enables input when it reaches zero.

const PIN_MIN = 4;
const PIN_MAX = 12;

export interface PinLoginProps {
  api: ApiClient;
  // Initial lockout from the bootstrap status probe. The component
  // surfaces it without forcing a failing login attempt first.
  initialLockSeconds?: number;
  onSuccess: (token: ParentTokenResponse) => void;
}

function digitsOnly(s: string): string {
  return s.replace(/\D+/g, "").slice(0, PIN_MAX);
}

// MM:SS for the countdown surface. 65 -> "1:05".
function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.ceil(totalSeconds));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function PinLogin(props: PinLoginProps): JSX.Element {
  const { api, onSuccess, initialLockSeconds = 0 } = props;

  const [pin, setPin] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lockSecondsRemaining, setLockSecondsRemaining] = useState<number>(
    initialLockSeconds > 0 ? initialLockSeconds : 0,
  );

  // AbortController spanning the component's lifetime. Constructed
  // inside an effect so React 18 StrictMode's double-mount gets a
  // fresh, un-aborted controller on the second mount — a ref-cached
  // controller would survive the first cleanup as already-aborted and
  // every fetch on the second mount would throw AbortError.
  const aborterRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
    };
  }, []);

  // Tick the countdown when the lock is engaged. Cleared on unmount or
  // on lock expiry.
  useEffect(() => {
    if (lockSecondsRemaining <= 0) return;
    const id = window.setInterval(() => {
      setLockSecondsRemaining((prev) => {
        if (prev <= 1) {
          // Re-enable input on expiry; clear stale lockout error.
          setFormError(null);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [lockSecondsRemaining]);

  const locked = lockSecondsRemaining > 0;

  const submit = useCallback(async (): Promise<void> => {
    if (locked) return;
    if (pin.length < PIN_MIN) {
      setFormError(`PIN must be at least ${PIN_MIN} digits.`);
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const aborter = aborterRef.current ?? new AbortController();
      const tokenResp = await api.issueParentToken(
        { pin },
        { signal: aborter.signal },
      );
      onSuccess(tokenResp);
    } catch (err) {
      if (isAbortError(err)) return;
      const lockedDetail = extractPinLockedDetail(err);
      if (lockedDetail !== null) {
        setLockSecondsRemaining(lockedDetail.seconds_until_unlock);
        setFormError(
          `PIN locked. Try again in ${formatCountdown(lockedDetail.seconds_until_unlock)}.`,
        );
        setPin("");
        return;
      }
      const invalidDetail = extractPinInvalidDetail(err);
      if (invalidDetail !== null) {
        setFormError(
          `Wrong PIN. ${invalidDetail.attempts_remaining} attempts remaining.`,
        );
        setPin("");
        return;
      }
      if (err instanceof ApiError) {
        setFormError(`login failed: ${err.status}`);
      } else if (err instanceof Error) {
        setFormError(`login failed: ${err.message}`);
      } else {
        setFormError("login failed");
      }
    } finally {
      setSubmitting(false);
    }
  }, [api, locked, onSuccess, pin]);

  return (
    <section
      data-testid="pin-login"
      style={{ maxWidth: 360, margin: "32px auto", padding: 16 }}
    >
      <h1 style={{ fontSize: 20 }}>Enter parent PIN</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="pin-login-pin" style={{ display: "block" }}>
            PIN
          </label>
          <input
            id="pin-login-pin"
            data-testid="pin-login-pin-input"
            type="password"
            inputMode="numeric"
            autoComplete="current-password"
            pattern="\d*"
            maxLength={PIN_MAX}
            value={pin}
            onChange={(e) => setPin(digitsOnly(e.target.value))}
            disabled={submitting || locked}
          />
        </div>
        {locked && (
          <div
            data-testid="pin-login-countdown"
            role="status"
            style={{ color: "#c0392b", fontSize: 13, marginBottom: 8 }}
          >
            PIN locked. Try again in {formatCountdown(lockSecondsRemaining)}.
          </div>
        )}
        {!locked && formError !== null && (
          <div
            data-testid="pin-login-form-error"
            role="alert"
            style={{ color: "#c0392b", fontSize: 13, marginBottom: 8 }}
          >
            {formError}
          </div>
        )}
        <button
          type="submit"
          data-testid="pin-login-submit"
          disabled={submitting || locked}
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </section>
  );
}
