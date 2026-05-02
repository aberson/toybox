import type { JSX } from "react";
import { useState } from "react";

import type { Activity } from "../api";

export interface SuggestionCardBusy {
  approve: boolean;
  skip: boolean;
  dismiss: boolean;
}

export interface SuggestionCardProps {
  activity: Activity;
  onApprove: () => Promise<void>;
  onSkip: () => Promise<void>;
  onDismiss: () => Promise<void>;
  // Optional in-flight flags so rapid double-clicks can't fire two
  // mutations with the same If-Match-Version. Defaults to all-idle.
  busy?: SuggestionCardBusy;
}

// Phase A scope: the "why this?" panel below is a STUB. Full
// implementation lands in Phase D Step 22 (signal weights + persona
// rationale). We render a non-clickable placeholder so the surface
// area exists for the future swap.
export function SuggestionCard(props: SuggestionCardProps): JSX.Element {
  const { activity, onApprove, onSkip, onDismiss } = props;
  const busy: SuggestionCardBusy = props.busy ?? {
    approve: false,
    skip: false,
    dismiss: false,
  };
  const [whyOpen, setWhyOpen] = useState(false);
  const title = activity.title ?? activity.summary ?? "Untitled activity";
  return (
    <section
      data-testid="suggestion-card"
      data-activity-id={activity.id}
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fafafa",
      }}
    >
      <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{title}</h2>
      <p style={{ margin: "0 0 8px 0", color: "#555", fontSize: 13 }}>
        v{activity.version} · {activity.state}
      </p>
      {activity.steps.length > 0 && (
        <ol
          style={{ marginTop: 8, fontSize: 14 }}
          data-testid="suggestion-steps"
        >
          {activity.steps.map((s) => (
            <li key={s.seq}>{s.body}</li>
          ))}
        </ol>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          type="button"
          data-testid="approve-button"
          disabled={busy.approve}
          onClick={() => {
            void onApprove();
          }}
        >
          {busy.approve ? "approving..." : "approve"}
        </button>
        <button
          type="button"
          data-testid="skip-button"
          disabled={busy.skip}
          onClick={() => {
            void onSkip();
          }}
        >
          {busy.skip ? "skipping..." : "skip"}
        </button>
        <button
          type="button"
          data-testid="dismiss-button"
          disabled={busy.dismiss}
          onClick={() => {
            void onDismiss();
          }}
        >
          {busy.dismiss ? "dismissing..." : "dismiss"}
        </button>
        <button
          type="button"
          data-testid="why-toggle"
          onClick={() => setWhyOpen((prev) => !prev)}
        >
          why this?
        </button>
      </div>
      {whyOpen && (
        <div
          data-testid="why-panel"
          style={{
            marginTop: 10,
            padding: 8,
            background: "#eef",
            fontSize: 13,
          }}
        >
          {/* TODO(phase-d-step-22): replace with signal weights + persona
              rationale. Phase A ships only this stub so the affordance
              exists for users and tests. */}
          (stub) full rationale ships in Phase D Step 22.
        </div>
      )}
    </section>
  );
}
