import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, extractValidationErrors, isAbortError } from "../api";
import type { ApiClient, ParentTokenResponse } from "../api";

// Step 21: first-run PIN setup. Two digits-only fields (PIN + confirm)
// are submitted to ``POST /api/auth/parent/setup``. On success the
// returned parent token is handed to the parent so the App can
// transition straight into the main flow without a second login.

// Mirrors the backend constraints in ``src/toybox/core/pin.py``. Kept
// inline (rather than imported) so the component remains self-
// contained for unit tests.
const PIN_MIN = 4;
const PIN_MAX = 12;

export interface PinSetupProps {
  api: ApiClient;
  onSuccess: (token: ParentTokenResponse) => void;
}

// Restrict input to digits-only on the way in. The backend also
// validates, but the keypad UX feels honest if a stray letter just
// disappears as you type rather than waiting for a 422.
function digitsOnly(s: string): string {
  return s.replace(/\D+/g, "").slice(0, PIN_MAX);
}

export function PinSetup(props: PinSetupProps): JSX.Element {
  const { api, onSuccess } = props;
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // Per-field errors (keyed by ``loc[1]`` from a 422 detail array). The
  // keys we render for are ``pin`` and ``confirm``.
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

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

  const submit = useCallback(async (): Promise<void> => {
    setFormError(null);
    setFieldErrors({});

    // Client-side preflight so the user doesn't burn a network
    // round-trip on a trivially-bad PIN.
    if (pin.length < PIN_MIN) {
      setFieldErrors({ pin: `PIN must be at least ${PIN_MIN} digits.` });
      return;
    }
    if (pin !== confirm) {
      setFieldErrors({ confirm: "PINs do not match." });
      return;
    }

    setSubmitting(true);
    try {
      // Effect populates the ref on mount; fall back to an inert
      // controller if submit somehow races a remount (cleanup just
      // aborted but mount-effect hasn't re-run yet).
      const aborter = aborterRef.current ?? new AbortController();
      const tokenResp = await api.setupPin(
        { pin, confirm },
        { signal: aborter.signal },
      );
      onSuccess(tokenResp);
    } catch (err) {
      if (isAbortError(err)) return;
      const validation = extractValidationErrors(err);
      if (validation !== null) {
        const map: Record<string, string> = {};
        for (const e of validation) {
          const field = e.loc.length >= 2 ? String(e.loc[1]) : String(e.loc[0]);
          if (!(field in map)) map[field] = e.msg;
        }
        setFieldErrors(map);
        setFormError("Please fix the errors below.");
      } else if (err instanceof ApiError) {
        // 409 ``pin_already_set`` lands here when a parallel setup
        // beat us to the row; the App will re-fetch status and the
        // PinLogin screen will replace this one.
        setFormError(`setup failed: ${err.status}`);
      } else if (err instanceof Error) {
        setFormError(`setup failed: ${err.message}`);
      } else {
        setFormError("setup failed");
      }
    } finally {
      setSubmitting(false);
    }
  }, [api, confirm, onSuccess, pin]);

  return (
    <section
      data-testid="pin-setup"
      style={{ maxWidth: 360, margin: "32px auto", padding: 16 }}
    >
      <h1 style={{ fontSize: 20 }}>Set parent PIN</h1>
      <p style={{ fontSize: 13, color: "#444" }}>
        Choose a {PIN_MIN}-{PIN_MAX} digit PIN. You will need this every
        time you open the parent UI.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="pin-setup-pin" style={{ display: "block" }}>
            PIN
          </label>
          <input
            id="pin-setup-pin"
            data-testid="pin-setup-pin-input"
            type="password"
            inputMode="numeric"
            autoComplete="new-password"
            pattern="\d*"
            maxLength={PIN_MAX}
            value={pin}
            onChange={(e) => setPin(digitsOnly(e.target.value))}
            disabled={submitting}
          />
          {fieldErrors["pin"] !== undefined && (
            <div
              data-testid="pin-setup-pin-error"
              style={{ color: "#c0392b", fontSize: 12 }}
            >
              {fieldErrors["pin"]}
            </div>
          )}
        </div>
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="pin-setup-confirm" style={{ display: "block" }}>
            Confirm PIN
          </label>
          <input
            id="pin-setup-confirm"
            data-testid="pin-setup-confirm-input"
            type="password"
            inputMode="numeric"
            autoComplete="new-password"
            pattern="\d*"
            maxLength={PIN_MAX}
            value={confirm}
            onChange={(e) => setConfirm(digitsOnly(e.target.value))}
            disabled={submitting}
          />
          {fieldErrors["confirm"] !== undefined && (
            <div
              data-testid="pin-setup-confirm-error"
              style={{ color: "#c0392b", fontSize: 12 }}
            >
              {fieldErrors["confirm"]}
            </div>
          )}
        </div>
        {formError !== null && (
          <div
            data-testid="pin-setup-form-error"
            role="alert"
            style={{ color: "#c0392b", fontSize: 13, marginBottom: 8 }}
          >
            {formError}
          </div>
        )}
        <button
          type="submit"
          data-testid="pin-setup-submit"
          disabled={submitting}
        >
          {submitting ? "Saving..." : "Save PIN"}
        </button>
      </form>
    </section>
  );
}
