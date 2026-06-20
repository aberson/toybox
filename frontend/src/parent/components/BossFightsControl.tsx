// Phase W Step W5: boss-fights flag toggle. WIRED setting — when On (the
// default), a dynamic adventure emits a distinct ``kind="boss_fight"``
// climax beat casting a boss-role toy; when Off the climax is an ordinary
// adventure_beat (W4 behavior). Mirrors the button-style segmented control
// used by GameLinearityControl inside SettingsPanel.tsx — here 2 buttons
// (On / Off), current selection highlighted via ``aria-pressed="true"`` +
// a selected background. Optimistic ``pendingValue`` state on click; on
// success bubble up via ``onValueChanged`` + clear pending; on error inline
// message + revert (no toast — matches the other SettingsPanel controls).
//
// State ownership: the parent (SettingsPanel, ultimately App.tsx) holds the
// source-of-truth ``currentValue``. This component owns nothing but the
// in-flight ``pendingValue`` for the active PUT.

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

interface BossChoice {
  value: boolean;
  label: string;
}

const BOSS_OPTIONS: readonly BossChoice[] = [
  { value: true, label: "On" },
  { value: false, label: "Off" },
];

export interface BossFightsControlProps {
  api: Pick<ApiClient, "setBossFightsEnabled">;
  currentValue: boolean;
  onValueChanged: (value: boolean) => void;
}

export function BossFightsControl(props: BossFightsControlProps): JSX.Element {
  const { api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Track the in-flight PUT so an unmount mid-request aborts it before its
  // .then/.catch can reach into a dead component.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Show the optimistic pending value while the PUT is in flight, then fall
  // back to the prop value (which the parent updates from the response).
  const displayedValue = pendingValue ?? currentValue;

  const handleClick = useCallback(
    (value: boolean): void => {
      if (pendingValue !== null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(value);
      setError(null);
      api
        .setBossFightsEnabled(value, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error ? err.message : "set boss fights failed";
          setError(message);
          setPendingValue(null);
        });
    },
    [api, onValueChanged, pendingValue],
  );

  return (
    <section data-testid="boss-fights-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Boss fights</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        When on, dynamic adventures end with an interactive boss-fight
        climax (a boss-role toy the kid must outsmart). Off skips the boss
        and runs ordinary beats to the end.
      </p>
      <div
        data-testid="boss-fights-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {BOSS_OPTIONS.map(({ value, label }) => {
          const active = displayedValue === value;
          const pending = pendingValue === value;
          return (
            <button
              key={label}
              type="button"
              data-testid={`boss-fights-${value ? "on" : "off"}`}
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
          data-testid="boss-fights-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
