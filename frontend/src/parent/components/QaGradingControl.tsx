// Phase W Step W3: Q&A answer-grading dial picker. WIRED setting — when
// not "Off", the advance path auto-grades a Q&A step's spoken answer
// against the recent transcript window and resolves the gate without a
// parent tap on a confident match. Mirrors the button-style segmented
// control used by ParentInvolvementControl inside SettingsPanel.tsx —
// 3 buttons (Off / Lenient / Strict), current selection highlighted via
// ``aria-pressed="true"`` + a selectedStyle background. Optimistic
// ``pendingValue`` state on click; on success bubble up via
// ``onValueChanged`` + clear pending; on error inline message + revert
// (no toast — matches the other SettingsPanel controls).
//
// State ownership: the parent (SettingsPanel, ultimately App.tsx) holds
// the source-of-truth ``currentValue``. This component owns nothing but
// the in-flight ``pendingValue`` for the active PUT.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient, QaGrading } from "../api";

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

interface QaGradingChoice {
  value: QaGrading;
  label: string;
}

// Canonical option set — mirrors backend ``QA_GRADING_VALID``
// (toybox/core/qa_grading.py). Order is rendered left-to-right; "off" is
// the default.
const QA_GRADING_OPTIONS: readonly QaGradingChoice[] = [
  { value: "off", label: "Off" },
  { value: "lenient", label: "Lenient" },
  { value: "strict", label: "Strict" },
];

export interface QaGradingControlProps {
  api: Pick<ApiClient, "setQaGrading">;
  currentValue: string;
  onValueChanged: (value: QaGrading) => void;
}

export function QaGradingControl(props: QaGradingControlProps): JSX.Element {
  const { api, currentValue, onValueChanged } = props;
  const [pendingValue, setPendingValue] = useState<QaGrading | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Tracks the in-flight PUT so an unmount mid-request aborts it before
  // its .then/.catch can reach into a dead component.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Show the optimistic pending value while the PUT is in flight, then
  // fall back to the prop value (which the parent updates from the
  // response via ``onValueChanged``).
  const displayedValue = pendingValue ?? currentValue;

  const handleClick = useCallback(
    (value: QaGrading): void => {
      if (pendingValue !== null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingValue(value);
      setError(null);
      api
        .setQaGrading(value, { signal: controller.signal })
        .then((resp) => {
          onValueChanged(resp.value);
          setPendingValue(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error ? err.message : "set qa grading failed";
          setError(message);
          setPendingValue(null);
        });
    },
    [api, onValueChanged, pendingValue],
  );

  return (
    <section data-testid="qa-grading-control" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Answer grading</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        When on, the kiosk listens for the child&apos;s spoken answer to a
        Q&amp;A step and advances automatically on a good answer. Lenient
        accepts partial answers; Strict needs the full answer.
      </p>
      <div
        data-testid="qa-grading-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {QA_GRADING_OPTIONS.map(({ value, label }) => {
          const active = displayedValue === value;
          const pending = pendingValue === value;
          return (
            <button
              key={value}
              type="button"
              data-testid={`qa-grading-${value}`}
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
          data-testid="qa-grading-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
