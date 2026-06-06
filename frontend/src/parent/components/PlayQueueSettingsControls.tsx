// Phase J step J10: segmented-button control for the play-queue
// target depth setting (how many proposed cards the queue tries to
// keep populated). Mirrors the TranscriptRetentionControl shape
// verbatim: 3 preset buttons, ``aria-pressed`` flips on click,
// optimistic ``pendingValue`` while the PUT is in flight, on success
// the parent reconciles via the ``onValueChanged`` callback, on
// rejection an inline error renders + the optimistic flip reverts.
//
// State ownership: the parent (SettingsPanel → App.tsx) holds the
// source-of-truth ``currentValue``. The component owns only the
// transient ``pendingValue`` for the active PUT.
//
// Snap-to-nearest for DISPLAY only: a backend skew or future preset
// migration could deliver a value outside the canonical set. Snapping
// keeps the "exactly one selected" aria-pressed contract from
// breaking; we never write the snapped value back.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type {
  ApiClient,
  PlayTargetDepth,
} from "../api";

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

const HINT_STYLE: CSSProperties = {
  fontSize: 11,
  color: "#6b7280",
  margin: "0 0 8px 0",
  lineHeight: 1.4,
};

interface PresetChoice<V extends number> {
  value: V;
  label: string;
}

// Canonical preset set — must match the literal-union type in
// api.ts (``PlayTargetDepth``) and the backend valid set in
// ``toybox/core/play_queue_settings.py``.
const TARGET_DEPTH_PRESETS: readonly PresetChoice<PlayTargetDepth>[] = [
  { value: 1, label: "1" },
  { value: 3, label: "3" },
  { value: 5, label: "5" },
];

// Snap an arbitrary value to the closest preset for DISPLAY only.
// Ties break toward the earlier preset (mirrors
// TranscriptRetentionControl's snap behavior).
function snapToPreset<V extends number>(
  value: number,
  presets: readonly PresetChoice<V>[],
): V {
  let best = presets[0]!.value;
  let bestDiff = Math.abs(value - best);
  for (const { value: candidate } of presets) {
    const diff = Math.abs(value - candidate);
    if (diff < bestDiff) {
      best = candidate;
      bestDiff = diff;
    }
  }
  return best;
}

export interface PlayTargetDepthControlProps {
  api: Pick<ApiClient, "setPlayTargetDepth">;
  currentValue: number;
  onValueChanged: (value: PlayTargetDepth) => void;
}

export function PlayTargetDepthControl(
  props: PlayTargetDepthControlProps,
): JSX.Element {
  const { api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] = useState<PlayTargetDepth | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  // Cancel any in-flight PUT on unmount so a late .then/.catch can't
  // call setState on a dead component. Mirrors
  // TranscriptRetentionControl's abort-on-unmount pattern.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const rawDisplayed = pendingValue ?? currentValue;
  const displayedValue = snapToPreset(rawDisplayed, TARGET_DEPTH_PRESETS);

  const handleClick = useCallback(
    (value: PlayTargetDepth): void => {
      if (pendingValue !== null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(value);
      setError(null);
      api
        .setPlayTargetDepth(value, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error
              ? err.message
              : "set play target depth failed";
          setError(message);
          setPendingValue(null);
        });
    },
    [api, onValueChanged, pendingValue],
  );

  return (
    <section data-testid="play-target-depth-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Play queue depth</h3>
      <p style={HINT_STYLE}>
        How many proposed play ideas the queue tries to keep ready for
        the kid. Higher values mean more variety; lower values keep
        the surface focused on one suggestion at a time.
      </p>
      <div
        data-testid="play-target-depth-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {TARGET_DEPTH_PRESETS.map(({ value, label }) => {
          const active = displayedValue === value;
          const pending = pendingValue === value;
          return (
            <button
              key={value}
              type="button"
              data-testid={`play-target-depth-${value}`}
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
          data-testid="play-target-depth-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
