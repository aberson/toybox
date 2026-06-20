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
  // Prominent primary CTA button. With the autonomous cadence loop
  // removed the manual trigger is the primary way to seed proposals,
  // so it gets a full-width, visually-weighty style.
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
        width: "100%",
        padding: "10px 16px",
        fontSize: 15,
        fontWeight: 600,
        background: disabled ? "#9ca3af" : "#1d4ed8",
        color: "#fff",
        border: "none",
        borderRadius: 6,
        cursor: busy ? "wait" : disabled ? "not-allowed" : "pointer",
        boxShadow: disabled ? "none" : "0 1px 3px rgba(0,0,0,0.2)",
        transition: "background 0.15s",
      }}
    >
      {busy ? "Loading…" : "Trigger now"}
    </button>
  );
}

export interface AdventureButtonProps {
  // Handler should POST /api/activities/propose with { adventure: true }
  // and push the resulting Activity into the store via applyMutationResult.
  onStart: () => Promise<void>;
  disabled?: boolean;
}

// Phase W Step W4: "Start an Adventure" trigger — a sibling of TriggerButton
// that seeds a dynamic, beat-by-beat generated adventure instead of a
// template-driven activity. Visually distinct (purple) so the parent can
// tell the two CTAs apart at a glance.
export function AdventureButton(props: AdventureButtonProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const click = async (): Promise<void> => {
    if (busy || props.disabled) return;
    setBusy(true);
    try {
      await props.onStart();
    } finally {
      setBusy(false);
    }
  };
  const disabled = busy || props.disabled === true;
  return (
    <button
      type="button"
      data-testid="adventure-button"
      onClick={() => {
        void click();
      }}
      disabled={disabled}
      style={{
        width: "100%",
        padding: "10px 16px",
        fontSize: 15,
        fontWeight: 600,
        background: disabled ? "#9ca3af" : "#7c3aed",
        color: "#fff",
        border: "none",
        borderRadius: 6,
        cursor: busy ? "wait" : disabled ? "not-allowed" : "pointer",
        boxShadow: disabled ? "none" : "0 1px 3px rgba(0,0,0,0.2)",
        transition: "background 0.15s",
      }}
    >
      {busy ? "Loading…" : "Start an Adventure"}
    </button>
  );
}
