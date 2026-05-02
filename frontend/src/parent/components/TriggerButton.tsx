import type { JSX } from "react";
import { useState } from "react";

export interface TriggerButtonProps {
  // Handler should POST /api/activities/propose with a stub intent and
  // push the resulting Activity into the store via setActivity.
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
  return (
    <button
      type="button"
      data-testid="trigger-button"
      onClick={() => {
        void click();
      }}
      disabled={busy || props.disabled === true}
      style={{
        padding: "10px 18px",
        fontSize: 15,
        background: "#1769aa",
        color: "white",
        border: "none",
        borderRadius: 4,
        cursor: busy ? "wait" : "pointer",
      }}
    >
      {busy ? "triggering..." : "Trigger play (manual)"}
    </button>
  );
}
