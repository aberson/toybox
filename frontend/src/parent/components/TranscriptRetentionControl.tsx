// Phase I step I3: transcript retention preset picker. Mirrors the
// button-style segmented control used by ListeningModeControl inside
// SettingsPanel.tsx — 5 buttons (1m / 3m / 5m / 10m / 15m), current
// selection highlighted via ``aria-pressed="true"`` + a selectedStyle
// background. Optimistic ``pendingSeconds`` state on click; on success
// bubble up via ``onSecondsChanged`` + clear pending; on error inline
// message + revert (no toast — matches the other SettingsPanel
// controls).
//
// State ownership: the parent (SettingsPanel, ultimately App.tsx) holds
// the source-of-truth ``currentSeconds``. This component owns nothing
// but the in-flight ``pendingSeconds`` for the active PUT.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient } from "../api";

const CARD_STYLE: CSSProperties = {
  border: "1px solid #e5e7eb",
  borderRadius: 6,
  padding: 8,
  background: "#fff",
  minWidth: 0,
};

const SECTION_HEADING_STYLE: CSSProperties = {
  fontSize: 13,
  margin: "0 0 6px 0",
  color: "#374151",
  fontWeight: 600,
};

interface RetentionChoice {
  seconds: number;
  label: string;
}

// Canonical preset set — mirrors backend ``RETENTION_SECONDS_VALID``
// (toybox/core/transcript_retention.py). Order is rendered left-to-
// right; 60s (1m) is the default.
const RETENTION_PRESETS: readonly RetentionChoice[] = [
  { seconds: 60, label: "1m" },
  { seconds: 180, label: "3m" },
  { seconds: 300, label: "5m" },
  { seconds: 600, label: "10m" },
  { seconds: 900, label: "15m" },
];

// Snap an arbitrary seconds value to the closest preset for DISPLAY
// only. Defensive against backend skew, test stubs, or a future
// migration that introduces a new preset before the frontend ships —
// the alternative (no button pressed) breaks the "exactly one
// selected" aria-pressed contract and leaves the UI silent. We never
// write the snapped value back; the source-of-truth stays whatever
// the backend reports.
function snapToPreset(seconds: number): number {
  let best = RETENTION_PRESETS[0]!.seconds;
  let bestDiff = Math.abs(seconds - best);
  for (const { seconds: candidate } of RETENTION_PRESETS) {
    const diff = Math.abs(seconds - candidate);
    if (diff < bestDiff) {
      best = candidate;
      bestDiff = diff;
    }
  }
  return best;
}

export interface TranscriptRetentionControlProps {
  api: Pick<ApiClient, "setTranscriptRetention">;
  currentSeconds: number;
  onSecondsChanged: (seconds: number) => void;
}

export function TranscriptRetentionControl(
  props: TranscriptRetentionControlProps,
): JSX.Element {
  const { api, currentSeconds, onSecondsChanged } = props;
  const [pendingSeconds, setPendingSeconds] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Tracks the in-flight PUT so an unmount mid-request aborts it
  // before its .then/.catch can reach into a dead component. Mirrors
  // the pattern in SettingsPanel.tsx (ImageGenModeToggle's GET-on-
  // mount uses the same controller-in-ref shape).
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Show the optimistic pending value while the PUT is in flight, then
  // fall back to the prop value (which the parent updates from the
  // response via ``onSecondsChanged``). If the prop is a non-canonical
  // value (e.g., a future preset the frontend doesn't yet know about),
  // snap to the closest known preset so the UI never goes silent.
  const rawDisplayed = pendingSeconds ?? currentSeconds;
  const displayedSeconds = snapToPreset(rawDisplayed);

  const handleClick = useCallback(
    (seconds: number): void => {
      if (pendingSeconds !== null) return;
      // Abort any previous in-flight PUT before starting a new one.
      // In practice the early-return above guards us from overlapping
      // PUTs from this component, but the unmount cleanup relies on
      // ``abortRef.current`` pointing at the current request.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingSeconds(seconds);
      setError(null);
      api
        .setTranscriptRetention(seconds, { signal: controller.signal })
        .then((resp) => {
          onSecondsChanged(resp.seconds);
          setPendingSeconds(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error
              ? err.message
              : "set transcript retention failed";
          setError(message);
          setPendingSeconds(null);
        });
    },
    [api, onSecondsChanged, pendingSeconds],
  );

  return (
    <section data-testid="transcript-retention-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Transcript retention</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        How long a transcript stays visible after the kid finishes
        speaking. After this many seconds the row fades out and is
        deleted from the database.
      </p>
      <div
        data-testid="transcript-retention-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {RETENTION_PRESETS.map(({ seconds, label }) => {
          const active = displayedSeconds === seconds;
          const pending = pendingSeconds === seconds;
          return (
            <button
              key={seconds}
              type="button"
              data-testid={`transcript-retention-${seconds}`}
              data-active={active ? "true" : "false"}
              aria-pressed={active ? "true" : "false"}
              disabled={pendingSeconds !== null}
              onClick={() => handleClick(seconds)}
              style={{
                fontSize: 11,
                padding: "4px 8px",
                borderRadius: 4,
                border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                background: active ? "#dbeafe" : "#fff",
                color: active ? "#1e3a8a" : "#374151",
                cursor: pendingSeconds !== null ? "default" : "pointer",
                fontWeight: active ? 600 : 400,
                opacity: pendingSeconds !== null && !pending ? 0.6 : 1,
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      {error !== null && (
        <div
          data-testid="transcript-retention-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
