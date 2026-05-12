import type { JSX } from "react";
import { useState } from "react";

export interface TriggerButtonProps {
  // Handler should POST /api/activities/propose with a stub intent and
  // push the resulting Activity into the store via applyMutationResult
  // (which routes the row into proposedList / active by its state).
  onTrigger: () => Promise<void>;
  disabled?: boolean;
}

export function TriggerButton(props: TriggerButtonProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const click = async (): Promise<void> => {
    if (busy || props.disabled) return;
    setBusy(true);
    try {
      await props.onTrigger();
    } finally {
      setBusy(false);
    }
  };
  // Phase J step J8: restyled to a small de-emphasized link affordance
  // ("+ trigger now"). Pre-J8 this was the prominent top-of-tab CTA;
  // with the autonomous cadence loop seeding proposals on its own the
  // manual trigger becomes a fallback. The button still uses
  // ``<button>`` for keyboard + a11y parity with the prior version —
  // only the styling moves to a text-link presentation.
  const disabled = busy || props.disabled === true;
  return (
    <button
      type="button"
      data-testid="trigger-button"
      onClick={() => {
        void click();
      }}
      disabled={disabled}
      style={{
        padding: 0,
        fontSize: 13,
        background: "transparent",
        color: disabled ? "#777" : "#1769aa",
        border: "none",
        textDecoration: "underline",
        cursor: busy ? "wait" : disabled ? "not-allowed" : "pointer",
      }}
    >
      {busy ? "loading…" : "+ trigger now"}
    </button>
  );
}
