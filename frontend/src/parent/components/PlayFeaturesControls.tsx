// Phase K step K2 (Phase L step L6): five parent-controlled feature-
// flag toggles. Originally eight; L5 removed play_embedded_enabled +
// play_endings_enabled + play_spontaneity_enabled when jokes/songs
// became per-activity reward types.
//
// Pattern mirrors PlayQueueSettingsControls.tsx (Phase J J10) — the
// parent (App.tsx → SettingsPanel) holds the source-of-truth bool
// state per flag, this component owns only the transient pendingValue
// for the active PUT. On success the parent reconciles via
// ``onValueChanged``; on rejection an inline error renders + the
// optimistic flip reverts.
//
// Visual style: a simple two-state segmented control (On / Off) per
// flag. Mirrors PlayQueueSettingsControls' segmented-button shape so
// SettingsPanel renders consistently. The five controls share a
// single component definition driven by the canonical flag list
// imported from ../api — one source of truth so a future sixth flag
// is a single-line edit (code-quality §2).
//
// Each toggle's aria-pressed reflects the *displayed* value (the
// pending click if in-flight, else the lifted currentValue). Pendings
// disable both buttons of the row so a double-click can't race two
// PUTs; once resolved, the row re-enables.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient, PhaseKFeatureFlag } from "../api";

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

const ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "6px 0",
  gap: 12,
  borderTop: "1px solid #f3f4f6",
};

// Single source of truth for the five flag setters routed from
// ``ApiClient``. The key is the canonical Pydantic name (matches the
// settings table row); the API client setter method name follows the
// same convention as Phase J's ``setPlayCadenceSeconds``. Phase L L5
// removed setPlayEmbeddedEnabled / setPlayEndingsEnabled /
// setPlaySpontaneityEnabled when those surfaces were deleted.
type FlagSetterName =
  | "setJokesEnabled"
  | "setSongsEnabled"
  | "setPlayStandaloneEnabled"
  | "setClickableWordsEnabled"
  | "setReadMeButtonEnabled";

interface FeatureToggleSpec {
  key: PhaseKFeatureFlag;
  // Operator-facing label per phase-k-plan §1 #6.
  label: string;
  // Short hint under the label — explains the surface in 1 sentence.
  hint: string;
  // ApiClient method to PUT a fresh value. Indirection via the
  // method-name string keeps this list flat-typed (no per-row union)
  // so render is one map call. The runtime call goes through
  // ``api[setter]`` — TS infers the boolean signature from the
  // ``Pick<ApiClient, FlagSetterName>`` constraint below.
  setter: FlagSetterName;
}

export const FEATURE_TOGGLES: readonly FeatureToggleSpec[] = [
  {
    key: "jokes_enabled",
    label: "Jokes enabled",
    hint: "Master switch for the jokes corpus. When off, no surface delivers a joke.",
    setter: "setJokesEnabled",
  },
  {
    key: "songs_enabled",
    label: "Songs enabled",
    hint: "Master switch for the songs corpus. When off, no surface delivers a song.",
    setter: "setSongsEnabled",
  },
  {
    key: "play_standalone_enabled",
    label: "Standalone joke/song activities",
    hint: '"Tell me a joke" / "Sing me a song" trigger phrases produce single-step activities.',
    setter: "setPlayStandaloneEnabled",
  },
  {
    key: "clickable_words_enabled",
    label: "Tap-to-read words",
    hint: "Tap any word on the kiosk to hear that word. When off, words render as plain text.",
    setter: "setClickableWordsEnabled",
  },
  {
    key: "read_me_button_enabled",
    label: "Read Me button",
    hint: "Watermarked Read Me bubble on each text-bearing step card. When off, the bubble is hidden.",
    setter: "setReadMeButtonEnabled",
  },
];

export interface PlayFeaturesControlsProps {
  api: Pick<ApiClient, FlagSetterName>;
  // The lifted, source-of-truth values for all five flags. Seeded by
  // App.tsx's bootstrap parallel-fetch; updated via
  // ``onValueChanged`` after each successful PUT.
  values: Record<PhaseKFeatureFlag, boolean>;
  // Bubble each successful PUT response back up so App.tsx can update
  // its lifted state. The kiosk also reads these for its own bootstrap
  // path; SettingsPanel uses ``values`` directly. Callback is a single
  // function (rather than five per-flag callbacks) so adding a sixth
  // flag stays a one-line edit.
  onValueChanged: (key: PhaseKFeatureFlag, value: boolean) => void;
}

interface FeatureToggleRowProps {
  spec: FeatureToggleSpec;
  api: Pick<ApiClient, FlagSetterName>;
  currentValue: boolean;
  onValueChanged: (key: PhaseKFeatureFlag, value: boolean) => void;
}

function FeatureToggleRow(props: FeatureToggleRowProps): JSX.Element {
  const { spec, api, currentValue, onValueChanged } = props;
  // Tracks the in-flight desired value (true/false) — null when idle.
  // Using a bool rather than a discriminator preserves the Phase J
  // pendingValue idiom: ``pending !== null`` means "PUT in flight".
  const [pendingValue, setPendingValue] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Cancel any in-flight PUT on unmount so a late .then/.catch can't
  // call setState on a dead component. Mirrors
  // PlayQueueSettingsControls' abort-on-unmount pattern.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const displayedValue = pendingValue ?? currentValue;

  const handleClick = useCallback(
    (next: boolean): void => {
      if (pendingValue !== null) return;
      if (next === currentValue) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(next);
      setError(null);
      // Setter shape is identical for all 5: ``(value, opts) =>
      // Promise<FeatureFlagResponse>``. Cast through `Pick<ApiClient,
      // FlagSetterName>` so we only need to widen at the boundary.
      //
      // IMPORTANT: invoke via ``.call(api, ...)`` rather than detaching
      // the method into a local. ApiClient's setters are regular
      // ``async setX(value, opts) { return this.request(...) }``
      // methods, NOT arrow-field methods — extracting them strips the
      // ``this`` binding and TypeScript-emitted ES2020 modules run in
      // strict mode, so ``setterFn(next, opts)`` would throw
      // ``Cannot read properties of undefined (reading 'request')`` on
      // every click. Mocked vi.fn ApiClients never tripped on this
      // because their method bodies don't reference ``this`` — exactly
      // the silent-wiring failure pattern code-quality §3/§4 warns
      // about. Iter-2 fix verified by the real-ApiClient integration
      // test in PlayFeaturesControls.integration.test.tsx.
      const setterFn = api[spec.setter] as (
        this: typeof api,
        value: boolean,
        opts?: { signal?: AbortSignal },
      ) => Promise<{ value: boolean }>;
      setterFn.call(api, next, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(spec.key, resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error ? err.message : `set ${spec.key} failed`;
          setError(message);
          setPendingValue(null);
        });
    },
    [api, currentValue, onValueChanged, pendingValue, spec],
  );

  return (
    <div
      data-testid={`feature-toggle-${spec.key}`}
      data-flag-value={displayedValue ? "true" : "false"}
      style={ROW_STYLE}
    >
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
          {spec.label}
        </div>
        <div style={{ fontSize: 11, color: "#6b7280", lineHeight: 1.3 }}>
          {spec.hint}
        </div>
        {error !== null && (
          <div
            data-testid={`feature-toggle-${spec.key}-error`}
            role="alert"
            style={{ color: "#b91c1c", fontSize: 11, marginTop: 4 }}
          >
            {error}
          </div>
        )}
      </div>
      <div
        style={{ display: "flex", gap: 4, flex: "0 0 auto" }}
        data-testid={`feature-toggle-${spec.key}-buttons`}
      >
        {[true, false].map((target) => {
          const active = displayedValue === target;
          const pending = pendingValue === target;
          return (
            <button
              key={target ? "on" : "off"}
              type="button"
              data-testid={`feature-toggle-${spec.key}-${target ? "on" : "off"}`}
              data-active={active ? "true" : "false"}
              aria-pressed={active ? "true" : "false"}
              disabled={pendingValue !== null}
              onClick={() => handleClick(target)}
              style={{
                fontSize: 11,
                padding: "4px 10px",
                borderRadius: 4,
                border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                background: active ? "#dbeafe" : "#fff",
                color: active ? "#1e3a8a" : "#374151",
                cursor: pendingValue !== null ? "default" : "pointer",
                fontWeight: active ? 600 : 400,
                opacity: pendingValue !== null && !pending ? 0.6 : 1,
              }}
            >
              {target ? "On" : "Off"}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function PlayFeaturesControls(
  props: PlayFeaturesControlsProps,
): JSX.Element {
  const { api, values, onValueChanged } = props;
  return (
    <section data-testid="play-features-controls" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Play features</h3>
      <p style={HINT_STYLE}>
        Master switches for the joke + song surfaces, the four play
        surfaces, and the kiosk word-level + Read Me affordances. A
        surface delivers content only when both its master and its
        surface flag are on.
      </p>
      {FEATURE_TOGGLES.map((spec) => (
        <FeatureToggleRow
          key={spec.key}
          spec={spec}
          api={api}
          currentValue={values[spec.key]}
          onValueChanged={onValueChanged}
        />
      ))}
    </section>
  );
}
