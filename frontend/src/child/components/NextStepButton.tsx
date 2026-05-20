import type { JSX } from "react";

export interface NextStepButtonProps {
  onClick: () => void;
  busy: boolean;
  // Label override. Defaults to "Next" when there is more to do and
  // "Done!" when the parent says we're on the last step.
  label?: string;
}

export function NextStepButton(props: NextStepButtonProps): JSX.Element {
  const label = props.label ?? "Next";
  return (
    <button
      type="button"
      data-testid="next-step-button"
      disabled={props.busy}
      onClick={props.onClick}
      style={{
        appearance: "none",
        border: "none",
        borderRadius: 9999,
        // Padding + fontSize + marginTop all use min(vw, vh) so short
        // viewports (iPad portrait with ElementCard on top) keep the
        // Next button on screen without scrolling.
        padding: "clamp(12px, 2vh, 24px) clamp(28px, 5vw, 56px)",
        fontSize: "clamp(1.1rem, min(3vw, 3vh), 1.75rem)",
        fontWeight: 700,
        color: "white",
        background: props.busy
          ? "#9aa0a6"
          : "linear-gradient(135deg, #1976d2 0%, #1565c0 100%)",
        boxShadow: "0 6px 18px rgba(25,118,210,0.35)",
        cursor: props.busy ? "default" : "pointer",
        // >=44pt per Apple HIG
        minWidth: 240,
        marginTop: "clamp(8px, 2vh, 32px)",
      }}
    >
      {props.busy ? "..." : label}
    </button>
  );
}
