import { useCallback, useState } from "react";
import type { CSSProperties, JSX } from "react";

import { unlockAudio } from "../sfx";

// Mirrors the backend's PIN format constraint
// (``src/toybox/core/pin.py``: 4-12 digits). Kept inline so the component
// stays self-contained.
const PIN_MIN = 4;
const PIN_MAX = 12;

// Outer layer: pinned to the viewport edges so the gradient bleeds into
// the iPad's rounded corners. No padding here — the inner content layer
// applies safe-area-inset padding instead.
const FULL_BLEED_BACKGROUND_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  margin: 0,
  background: "linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)",
  boxSizing: "border-box",
  overflow: "hidden",
};

// Inner layer: centered content. env(safe-area-inset-*) clears the
// iPad camera notch / home indicator; the 32px fallback matches prior
// desktop rendering.
const FULL_BLEED_CONTENT_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 24,
  fontFamily: "system-ui, sans-serif",
  width: "100%",
  height: "100%",
  boxSizing: "border-box",
  padding:
    "env(safe-area-inset-top, 32px) env(safe-area-inset-right, 32px) env(safe-area-inset-bottom, 32px) env(safe-area-inset-left, 32px)",
};

function digitsOnly(s: string): string {
  return s.replace(/\D+/g, "").slice(0, PIN_MAX);
}

export interface KioskPinPromptProps {
  onSubmit: (pin: string) => void;
  // Server-side error to surface alongside the form (e.g. "Wrong PIN").
  // Format errors caught client-side render below the input under their
  // own data-testid; this prop is for messages the parent App injects
  // after a failed bootstrap.
  errorMessage?: string;
}

export function KioskPinPrompt(props: KioskPinPromptProps): JSX.Element {
  const { onSubmit, errorMessage } = props;
  const [pin, setPin] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const submit = useCallback((): void => {
    if (pin.length < PIN_MIN) {
      setFormError(`PIN must be at least ${PIN_MIN} digits.`);
      return;
    }
    setFormError(null);
    // iOS Safari requires audio to be unlocked from inside a user
    // gesture handler. Submitting the PIN is the kiosk's first
    // guaranteed user-gesture event, so prime the SFX cache here
    // before the bootstrap kicks in (which happens off-gesture).
    unlockAudio();
    onSubmit(pin);
  }, [pin, onSubmit]);

  return (
    <main data-testid="kiosk-pin-prompt" style={FULL_BLEED_BACKGROUND_STYLE}>
      <div style={FULL_BLEED_CONTENT_STYLE}>
        <h1
          style={{
            fontSize: 22,
            margin: 0,
            color: "#333",
            textAlign: "center",
          }}
        >
          Enter parent PIN
        </h1>
        <p
          style={{
            fontSize: 13,
            color: "#666",
            textAlign: "center",
            maxWidth: 360,
            margin: 0,
          }}
        >
          The kiosk needs the parent PIN to start. It will be saved in this
          browser.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            alignItems: "center",
          }}
        >
          <input
            data-testid="kiosk-pin-prompt-input"
            type="password"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d*"
            maxLength={PIN_MAX}
            value={pin}
            onChange={(e) => setPin(digitsOnly(e.target.value))}
            style={{
              fontSize: 24,
              padding: "8px 12px",
              width: 200,
              // >=44pt per Apple HIG
              minHeight: 44,
              textAlign: "center",
              border: "1px solid #ccc",
              borderRadius: 6,
            }}
            autoFocus
          />
          {formError !== null && (
            <div
              data-testid="kiosk-pin-prompt-error"
              role="alert"
              style={{ color: "#c0392b", fontSize: 13 }}
            >
              {formError}
            </div>
          )}
          {errorMessage !== undefined && formError === null && (
            <div
              data-testid="kiosk-pin-prompt-server-error"
              role="alert"
              style={{ color: "#c0392b", fontSize: 13 }}
            >
              {errorMessage}
            </div>
          )}
          <button
            type="submit"
            data-testid="kiosk-pin-prompt-submit"
            style={{
              padding: "8px 24px",
              fontSize: 16,
              // >=44pt per Apple HIG
              minHeight: 44,
              minWidth: 44,
              background: "#3a82f6",
              color: "white",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
            }}
          >
            Save PIN
          </button>
        </form>
      </div>
    </main>
  );
}
