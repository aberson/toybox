import type { JSX } from "react";

import type { MicState } from "../store";

export interface HeaderProps {
  micState: MicState;
  muted: boolean;
  onToggleMute: () => void;
  wsState: string;
}

const MIC_LABELS: Record<MicState, string> = {
  capturing: "capturing",
  paused: "paused",
  error: "error",
};

const MIC_COLORS: Record<MicState, string> = {
  capturing: "#1a8c2a", // green
  paused: "#9aa0a6", // grey
  error: "#c62828", // red
};

export function Header(props: HeaderProps): JSX.Element {
  const { micState, muted, onToggleMute, wsState } = props;
  const dotColor = MIC_COLORS[micState];
  const label = MIC_LABELS[micState];
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        borderBottom: "1px solid #ddd",
      }}
    >
      <h1 style={{ margin: 0, fontSize: 18 }}>Toybox Parent</h1>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          aria-label={`mic-${label}`}
          data-testid="mic-indicator"
          data-mic-state={micState}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: dotColor,
            }}
          />
          <span style={{ fontSize: 13 }}>mic: {label}</span>
        </div>
        <button
          type="button"
          onClick={onToggleMute}
          aria-pressed={muted}
          data-testid="mic-mute-toggle"
        >
          {muted ? "unmute" : "mute"}
        </button>
        <span style={{ fontSize: 12, color: "#555" }} data-testid="ws-state">
          ws: {wsState}
        </span>
      </div>
    </header>
  );
}
