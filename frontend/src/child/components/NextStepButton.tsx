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
        padding: "24px 56px",
        fontSize: "clamp(1.5rem, 3vw, 2.25rem)",
        fontWeight: 700,
        color: "white",
        background: props.busy
          ? "#9aa0a6"
          : "linear-gradient(135deg, #1976d2 0%, #1565c0 100%)",
        boxShadow: "0 6px 18px rgba(25,118,210,0.35)",
        cursor: props.busy ? "default" : "pointer",
        // >=44pt per Apple HIG
        minWidth: 240,
        marginTop: 32,
      }}
    >
      {props.busy ? "..." : label}
    </button>
  );
}
