import type { JSX } from "react";

import type { Activity } from "../api";

export interface ActivityPanelBusy {
  regenerate: boolean;
  end: boolean;
  didntWork: boolean;
}

export interface ActivityPanelProps {
  activity: Activity;
  onRegenerate: () => Promise<void>;
  onEnd: () => Promise<void>;
  onDidntWork: () => Promise<void>;
  // Optional in-flight flags. Same idea as SuggestionCard's busy: keep
  // a rapid second click from racing the first with the same version.
  busy?: ActivityPanelBusy;
}

export function ActivityPanel(props: ActivityPanelProps): JSX.Element {
  const { activity, onRegenerate, onEnd, onDidntWork } = props;
  const busy: ActivityPanelBusy = props.busy ?? {
    regenerate: false,
    end: false,
    didntWork: false,
  };
  const title = activity.title ?? activity.summary ?? "Activity";
  return (
    <section
      data-testid="activity-panel"
      data-activity-id={activity.id}
      data-activity-state={activity.state}
      style={{
        border: "1px solid #1769aa",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "white",
      }}
    >
      <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{title}</h2>
      <p style={{ margin: "0 0 8px 0", color: "#555", fontSize: 13 }}>
        state: {activity.state} · v{activity.version}
      </p>
      {activity.steps.length > 0 && (
        <ol style={{ marginTop: 8, fontSize: 14 }} data-testid="activity-steps">
          {activity.steps.map((s) => (
            <li
              key={s.seq}
              style={{ fontWeight: s.current ? 700 : 400 }}
              data-current={s.current ? "true" : undefined}
            >
              {s.body}
            </li>
          ))}
        </ol>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          type="button"
          data-testid="regenerate-button"
          disabled={busy.regenerate}
          onClick={() => {
            void onRegenerate();
          }}
        >
          {busy.regenerate ? "skipping..." : "skip & try another"}
        </button>
        <button
          type="button"
          data-testid="end-button"
          disabled={busy.end}
          onClick={() => {
            void onEnd();
          }}
        >
          {busy.end ? "ending..." : "end"}
        </button>
        <button
          type="button"
          data-testid="didnt-work-button"
          disabled={busy.didntWork}
          onClick={() => {
            void onDidntWork();
          }}
        >
          {busy.didntWork ? "marking..." : "didn't work"}
        </button>
      </div>
    </section>
  );
}
