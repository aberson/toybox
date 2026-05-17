// Phase L Step L8: Rewards section in the parent Kids & Toyboxes tab.
//
// Houses the L7 RewardIngest panel (create / edit / archive picture
// rewards) plus the two master toggles ``jokes_enabled`` +
// ``songs_enabled`` that L8 moved out of PlayFeaturesControls. The
// rationale per phase-l-plan: jokes / songs are now per-activity reward
// TYPES (alongside picture rewards), so their master switches live
// alongside the reward library rather than under "Play features".
//
// Plumbing mirrors PlayFeaturesControls:
//
//   * Parent (App.tsx) owns the source-of-truth ``featureFlags`` dict.
//   * This component receives the two relevant values + an
//     ``onValueChanged(key, value)`` callback per the same shape App
//     already uses for PlayFeaturesControls.
//   * The toggle row itself owns a transient ``pendingValue`` so a PUT
//     in flight disables both buttons (prevents double-click races) and
//     re-enables on response.
//   * Each toggle's ``aria-pressed`` reflects the *displayed* value
//     (the pending click if in-flight, else the lifted currentValue).
//
// One-line hints under each toggle match the L8 spec:
//   * "Jokes can fire as activity-end rewards (and standalone if
//     enabled)"
//   * "Songs can fire as activity-end rewards (and standalone if
//     enabled)"
//
// Setter invocation uses ``setterFn.call(api, value, opts)`` — same
// this-binding trap PlayFeaturesControls' iter-1 review caught
// (code-quality §3/§4). Detaching the method strips ``this`` and the
// real ApiClient setter then throws inside the .then chain on every
// click. Calling via .call(api, ...) preserves the binding.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient, PhaseKFeatureFlag } from "../api";
import { RewardIngest } from "./RewardIngest";

// Setter method names this section needs from the ApiClient. Kept as a
// string-literal union so the ``api`` prop can be a narrow
// ``Pick<ApiClient, ...>`` — same convention PlayFeaturesControls uses.
type FlagSetterName = "setJokesEnabled" | "setSongsEnabled";

// Single source of truth for the two reward-master toggles in this
// section's header. Adding a future third master (e.g. a "stories"
// reward type) is a single-row addition here. The spec list shape
// matches PlayFeaturesControls' FeatureToggleSpec so a future
// refactor could extract a shared FeatureToggleRow.
interface RewardMasterToggleSpec {
  key: PhaseKFeatureFlag;
  label: string;
  // Short hint under the label — one sentence per spec.
  hint: string;
  setter: FlagSetterName;
}

export const REWARD_MASTER_TOGGLES: readonly RewardMasterToggleSpec[] = [
  {
    key: "jokes_enabled",
    label: "Jokes enabled",
    hint: "Jokes can fire as activity-end rewards (and standalone if enabled)",
    setter: "setJokesEnabled",
  },
  {
    key: "songs_enabled",
    label: "Songs enabled",
    hint: "Songs can fire as activity-end rewards (and standalone if enabled)",
    setter: "setSongsEnabled",
  },
];

const SECTION_STYLE: CSSProperties = {
  border: "1px solid #ccc",
  borderRadius: 6,
  padding: 16,
  margin: "12px 0",
  background: "#fff",
};

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  marginBottom: 12,
};

const HEADER_TOGGLES_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
  gap: 8,
  marginTop: 8,
};

const TOGGLE_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "6px 8px",
  gap: 12,
  border: "1px solid #e5e7eb",
  borderRadius: 4,
  background: "#fafafa",
};

export interface RewardsSectionProps {
  // Narrow Pick of ApiClient: RewardIngest needs the full
  // listRewards/uploadReward/confirmReward/updateReward subset, so we
  // accept the full ApiClient and forward it. The two master-toggle
  // setters are pulled off the same instance.
  api: ApiClient;
  // Lifted, source-of-truth values for the two master toggles. Seeded
  // by App.tsx's bootstrap parallel-fetch alongside the surviving
  // PlayFeaturesControls flags.
  values: Record<PhaseKFeatureFlag, boolean>;
  // Bubble each successful PUT response back up so App.tsx updates the
  // lifted ``featureFlags`` dict. The callback signature matches
  // ``PlayFeaturesControlsProps.onValueChanged`` so the parent's
  // single ``handleFeatureFlagChanged`` reducer routes both surfaces.
  onValueChanged: (key: PhaseKFeatureFlag, value: boolean) => void;
}

interface RewardMasterToggleRowProps {
  spec: RewardMasterToggleSpec;
  api: Pick<ApiClient, FlagSetterName>;
  currentValue: boolean;
  onValueChanged: (key: PhaseKFeatureFlag, value: boolean) => void;
}

function RewardMasterToggleRow(
  props: RewardMasterToggleRowProps,
): JSX.Element {
  const { spec, api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Cancel any in-flight PUT on unmount. Mirrors
  // PlayFeaturesControls' abort-on-unmount pattern so a late .then /
  // .catch can't call setState on a dead component.
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
      // IMPORTANT: invoke via ``.call(api, ...)`` so the ``this`` binding
      // is preserved. The same trap PlayFeaturesControls' iter-1 review
      // surfaced (code-quality §3/§4): detached setters drop ``this``,
      // and the real ApiClient setter's ``return this.request(...)``
      // throws. Mocked vi.fn setters don't trip on this so the unit
      // test alone wouldn't have caught a regression.
      const setterFn = api[spec.setter] as (
        this: typeof api,
        value: boolean,
        opts?: { signal?: AbortSignal },
      ) => Promise<{ value: boolean }>;
      setterFn
        .call(api, next, { signal: controller.signal })
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
      data-testid={`reward-master-toggle-${spec.key}`}
      data-flag-value={displayedValue ? "true" : "false"}
      style={TOGGLE_ROW_STYLE}
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
            data-testid={`reward-master-toggle-${spec.key}-error`}
            role="alert"
            style={{ color: "#b91c1c", fontSize: 11, marginTop: 4 }}
          >
            {error}
          </div>
        )}
      </div>
      <div
        style={{ display: "flex", gap: 4, flex: "0 0 auto" }}
        data-testid={`reward-master-toggle-${spec.key}-buttons`}
      >
        {[true, false].map((target) => {
          const active = displayedValue === target;
          const pending = pendingValue === target;
          return (
            <button
              key={target ? "on" : "off"}
              type="button"
              data-testid={`reward-master-toggle-${spec.key}-${target ? "on" : "off"}`}
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

export function RewardsSection(props: RewardsSectionProps): JSX.Element {
  const { api, values, onValueChanged } = props;
  return (
    <section data-testid="rewards-section" style={SECTION_STYLE}>
      <header style={HEADER_STYLE}>
        <h2 style={{ margin: 0, fontSize: 17 }}>Rewards</h2>
        <p style={{ color: "#666", fontSize: 12, margin: 0 }}>
          Master switches for joke + song reward types, plus the picture
          reward library below.
        </p>
        <div
          style={HEADER_TOGGLES_STYLE}
          data-testid="reward-master-toggles"
        >
          {REWARD_MASTER_TOGGLES.map((spec) => (
            <RewardMasterToggleRow
              key={spec.key}
              spec={spec}
              api={api}
              currentValue={values[spec.key]}
              onValueChanged={onValueChanged}
            />
          ))}
        </div>
      </header>
      <RewardIngest api={api} />
    </section>
  );
}
