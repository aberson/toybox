import { useCallback, useState } from "react";
import type { CSSProperties, JSX } from "react";

// Mirrors the backend's PIN format constraint
// (``src/toybox/core/pin.py``: 4-12 digits). Kept inline so the component
// stays self-contained.
const PIN_MIN = 4;
const PIN_MAX = 12;

const FULL_BLEED_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  margin: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 24,
  background: "linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)",
  fontFamily: "system-ui, sans-serif",
  padding: 32,
  boxSizing: "border-box",
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
    onSubmit(pin);
  }, [pin, onSubmit]);

  return (
    <main data-testid="kiosk-pin-prompt" style={FULL_BLEED_STYLE}>
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
    </main>
  );
}
