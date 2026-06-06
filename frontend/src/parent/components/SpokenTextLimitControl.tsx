// Phase R Step R2: spoken text character limit preset picker. Mirrors the
// button-style segmented control used by TranscriptRetentionControl inside
// SettingsPanel.tsx — 5 buttons (off / 50 / 100 / 150 / 250), current
// selection highlighted via ``aria-pressed="true"`` + a selectedStyle
// background. Optimistic ``pendingValue`` state on click; on success
// bubble up via ``onValueChanged`` + clear pending; on error inline
// message + revert (no toast — matches the other SettingsPanel controls).
//
// State ownership: the parent (SettingsPanel, ultimately App.tsx) holds
// the source-of-truth ``currentValue``. This component owns nothing
// but the in-flight ``pendingValue`` for the active PUT.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient, SpokenTextLimit } from "../api";

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

interface LimitChoice {
  value: SpokenTextLimit;
  label: string;
}

// Canonical preset set — mirrors backend ``SPOKEN_TEXT_LIMIT_VALID``
// (toybox/core/spoken_text_limit.py). Order is rendered left-to-right;
// 150 is the default.
const LIMIT_PRESETS: readonly LimitChoice[] = [
  { value: 0, label: "off" },
  { value: 50, label: "50" },
  { value: 100, label: "100" },
  { value: 150, label: "150" },
  { value: 250, label: "250" },
];

// Snap an arbitrary value to the closest preset for DISPLAY only.
// Defensive against backend skew, test stubs, or a future migration
// that introduces a new preset before the frontend ships.
function snapToPreset(value: number): SpokenTextLimit {
  let best = LIMIT_PRESETS[0]!.value;
  let bestDiff = Math.abs(value - best);
  for (const { value: candidate } of LIMIT_PRESETS) {
    const diff = Math.abs(value - candidate);
    if (diff < bestDiff) {
      best = candidate;
      bestDiff = diff;
    }
  }
  return best;
}

export interface SpokenTextLimitControlProps {
  api: Pick<ApiClient, "setSpokenTextLimit">;
  currentValue: number;
  onValueChanged: (value: SpokenTextLimit) => void;
}

export function SpokenTextLimitControl(
  props: SpokenTextLimitControlProps,
): JSX.Element {
  const { api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] = useState<SpokenTextLimit | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Tracks the in-flight PUT so an unmount mid-request aborts it
  // before its .then/.catch can reach into a dead component.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Show the optimistic pending value while the PUT is in flight, then
  // fall back to the prop value (which the parent updates from the
  // response via ``onValueChanged``). If the prop is a non-canonical
  // value, snap to the closest known preset so the UI never goes silent.
  const rawDisplayed = pendingValue ?? currentValue;
  const displayedValue = snapToPreset(rawDisplayed);

  const handleClick = useCallback(
    (value: SpokenTextLimit): void => {
      if (pendingValue !== null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(value);
      setError(null);
      api
        .setSpokenTextLimit(value, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error
              ? err.message
              : "set spoken text limit failed";
          setError(message);
          setPendingValue(null);
        });
    },
    [api, onValueChanged, pendingValue],
  );

  return (
    <section data-testid="spoken-text-limit-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Read Me limit</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        Maximum characters spoken when the kid taps Read Me. Text is
        truncated at a word boundary; the full text stays visible on
        screen. &quot;off&quot; speaks the full body.
      </p>
      <div
        data-testid="spoken-text-limit-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {LIMIT_PRESETS.map(({ value, label }) => {
          const active = displayedValue === value;
          const pending = pendingValue === value;
          return (
            <button
              key={value}
              type="button"
              data-testid={`spoken-text-limit-${value}`}
              data-active={active ? "true" : "false"}
              aria-pressed={active ? "true" : "false"}
              disabled={pendingValue !== null}
              onClick={() => handleClick(value)}
              style={{
                fontSize: 11,
                padding: "4px 8px",
                borderRadius: 4,
                border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                background: active ? "#dbeafe" : "#fff",
                color: active ? "#1e3a8a" : "#374151",
                cursor: pendingValue !== null ? "default" : "pointer",
                fontWeight: active ? 600 : 400,
                opacity: pendingValue !== null && !pending ? 0.6 : 1,
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      {error !== null && (
        <div
          data-testid="spoken-text-limit-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
