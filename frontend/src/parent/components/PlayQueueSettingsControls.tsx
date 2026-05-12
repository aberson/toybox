// Phase J step J10: segmented-button controls for the two play-queue
// household settings — target depth (how many proposed cards the queue
// tries to keep populated) and cadence seconds (how often the
// autonomous loop fires; 0 = disabled). Both mirror the
// TranscriptRetentionControl shape verbatim: 3-5 preset buttons,
// ``aria-pressed`` flips on click, optimistic ``pendingValue`` while
// the PUT is in flight, on success the parent reconciles via the
// ``onValueChanged`` callback, on rejection an inline error renders +
// the optimistic flip reverts.
//
// State ownership: the parent (SettingsPanel → App.tsx) holds the
// source-of-truth ``currentValue``. The components own only the
// transient ``pendingValue`` for the active PUT.
//
// Snap-to-nearest for DISPLAY only: a backend skew or future preset
// migration could deliver a value outside the canonical set. Snapping
// keeps the "exactly one selected" aria-pressed contract from
// breaking; we never write the snapped value back. For the cadence
// control specifically, 0 is a real in-set value (cadence disabled),
// so snap-to-nearest considers it the same way as any other preset
// — there is no falsy short-circuit anywhere on this path.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type {
  ApiClient,
  PlayCadenceSeconds,
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

// Canonical preset sets — must match the literal-union types in
// api.ts (``PlayTargetDepth`` + ``PlayCadenceSeconds``) and the
// backend valid sets in ``toybox/core/play_queue_settings.py``.
const TARGET_DEPTH_PRESETS: readonly PresetChoice<PlayTargetDepth>[] = [
  { value: 1, label: "1" },
  { value: 3, label: "3" },
  { value: 5, label: "5" },
];

const CADENCE_PRESETS: readonly PresetChoice<PlayCadenceSeconds>[] = [
  { value: 0, label: "off" },
  { value: 10, label: "10s" },
  { value: 30, label: "30s" },
  { value: 60, label: "1m" },
];

// Snap an arbitrary value to the closest preset for DISPLAY only.
// Ties break toward the earlier preset (mirrors
// TranscriptRetentionControl's snap behavior). The cadence variant
// treats 0 as a real value — the comparison is plain numeric distance,
// so a backend response of (e.g.) 5 would snap to 0 not 10 (diff 5
// vs 5, tie → earlier).
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

export interface PlayCadenceSecondsControlProps {
  api: Pick<ApiClient, "setPlayCadenceSeconds">;
  currentValue: number;
  onValueChanged: (value: PlayCadenceSeconds) => void;
}

export function PlayCadenceSecondsControl(
  props: PlayCadenceSecondsControlProps,
): JSX.Element {
  const { api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] =
    useState<PlayCadenceSeconds | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // The strict null-coalesce here matters: ``pendingValue === 0`` is a
  // valid in-flight choice (cadence off) and MUST NOT be treated as
  // "no pending PUT". The ``?? currentValue`` operator only takes the
  // right branch when the left is null/undefined, so 0 round-trips
  // through display correctly. A ``pendingValue || currentValue`` here
  // would silently clobber the off state — caught by the off
  // round-trip test below.
  const rawDisplayed = pendingValue ?? currentValue;
  const displayedValue = snapToPreset(rawDisplayed, CADENCE_PRESETS);

  const handleClick = useCallback(
    (value: PlayCadenceSeconds): void => {
      if (pendingValue !== null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(value);
      setError(null);
      api
        .setPlayCadenceSeconds(value, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error
              ? err.message
              : "set play cadence failed";
          setError(message);
          setPendingValue(null);
        });
    },
    [api, onValueChanged, pendingValue],
  );

  return (
    <section data-testid="play-cadence-seconds-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Play queue cadence</h3>
      <p style={HINT_STYLE}>
        How often the autonomous loop proposes a new play idea. When
        off, only transcripts + the manual Trigger fire.
      </p>
      <div
        data-testid="play-cadence-seconds-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {CADENCE_PRESETS.map(({ value, label }) => {
          const active = displayedValue === value;
          const pending = pendingValue === value;
          return (
            <button
              key={value}
              type="button"
              data-testid={`play-cadence-seconds-${value}`}
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
          data-testid="play-cadence-seconds-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
