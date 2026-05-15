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

// Step 23: the "why this?" panel renders the trigger phrase that fired
// the suggestion plus the persona-match rationale. Both are pulled off
// the activity wire shape (``trigger_phrase``, ``persona_reasoning``).
// The intent (``intent_source``) is also surfaced as a third row when
// available, since the slot/intent drove the template selection.
export function SuggestionCard(props: SuggestionCardProps): JSX.Element {
  const { activity, onApprove, onSkip, onDismiss } = props;
  const busy: SuggestionCardBusy = props.busy ?? {
    approve: false,
    skip: false,
    dismiss: false,
  };
  const [whyOpen, setWhyOpen] = useState(false);
  const title = activity.title ?? activity.summary ?? "Untitled activity";
  const personaMeta = (activity.metadata as Record<string, unknown>)["persona"];
  const personaName =
    typeof personaMeta === "object" &&
    personaMeta !== null &&
    typeof (personaMeta as Record<string, unknown>)["display_name"] === "string"
      ? ((personaMeta as Record<string, unknown>)["display_name"] as string)
      : null;

  const triggerPhrase = activity.trigger_phrase;
  const personaReasoning = activity.persona_reasoning;
  const intentSource = activity.intent_source;

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
      {personaName !== null && (
        <p
          data-testid="suggestion-persona"
          style={{ margin: "0 0 4px 0", color: "#1769aa", fontSize: 13 }}
        >
          persona: {personaName}
        </p>
      )}
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
          {busy.skip ? "swapping..." : "try a different one"}
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
          aria-expanded={whyOpen}
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
          {/* Step 23: render trigger phrase, persona rationale, and the
              intent that drove template selection. ``trigger_phrase`` is
              null when the activity was proposed manually (no transcript
              match); we show a soft "no trigger" line in that case so
              parents don't see an empty section. ``persona_reasoning``
              is always populated by the backend (synthesised default
              when the propose call didn't supply one).

              ``typeof === "string"`` (not ``!== null``) because activities
              that arrive via the ``activity.state`` WS envelope have these
              fields stripped as PII (api/activities.py:_emit_state), so
              they reach the frontend as ``undefined`` rather than
              ``null``. A plain ``!== null`` check would let undefined
              through and the template literal would render the literal
              string "undefined". (#111) */}
          <div data-testid="why-trigger" style={{ marginBottom: 4 }}>
            <strong>trigger:</strong>{" "}
            {typeof triggerPhrase === "string" && triggerPhrase !== ""
              ? `"${triggerPhrase}"`
              : "(no trigger — proposed manually)"}
          </div>
          <div data-testid="why-persona" style={{ marginBottom: 4 }}>
            <strong>persona:</strong>{" "}
            {typeof personaReasoning === "string" && personaReasoning !== ""
              ? personaReasoning
              : "matched on intent"}
          </div>
          {intentSource !== null && intentSource !== "" && (
            <div data-testid="why-intent">
              <strong>intent:</strong> {intentSource}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
