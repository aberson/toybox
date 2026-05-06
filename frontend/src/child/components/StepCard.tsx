import type { JSX } from "react";

import type { Activity } from "../api";
import { ToyActionSprite } from "./ToyActionSprite";

export interface StepCardProps {
  activity: Activity;
}

// Best-effort lookup for the toy display name on the activity envelope.
// The wire ``Activity`` doesn't (yet) carry a hydrated toy summary; if
// the parent attaches one under ``metadata.toys`` (a list of
// ``{id, display_name}`` objects) we surface the matching name for a11y.
// Falls back to undefined which makes ``ToyActionSprite`` render its
// alt as the bare slot key. Tolerant of any shape — the function never
// throws because a kiosk render path can't recover from one.
function lookupToyDisplayName(
  activity: Activity,
  toyId: string,
): string | undefined {
  const toys = activity.metadata?.["toys"];
  if (!Array.isArray(toys)) return undefined;
  for (const entry of toys) {
    if (typeof entry !== "object" || entry === null) continue;
    const rec = entry as Record<string, unknown>;
    if (rec["id"] === toyId) {
      const dn = rec["display_name"];
      if (typeof dn === "string" && dn.length > 0) return dn;
    }
  }
  return undefined;
}

// The kiosk renders the step flagged `current: true`. While the
// activity is `approved` (parent has approved but the child hasn't
// pressed the button yet) no step is current — we render the first
// step's body as a "ready to start" hint so the kiosk isn't blank.
export function StepCard(props: StepCardProps): JSX.Element {
  const { activity } = props;
  const currentStep = activity.steps.find((s) => s.current) ?? null;
  const previewStep = currentStep ?? activity.steps[0] ?? null;
  const totalSteps = activity.steps.length;
  const currentIndex =
    currentStep !== null
      ? activity.steps.findIndex((s) => s.seq === currentStep.seq) + 1
      : 0;

  // Phase F Step F7 sprite resolution. Both signals must be present
  // for the sprite to render: the step itself must opt in via
  // ``action_slot``, and the activity must carry at least one toy
  // (``toy_ids[0]`` is the deterministic pick — multi-toy
  // composition is out of scope for v1, see plan §"Activity → toy
  // resolution"). When either is missing the kiosk renders the same
  // body-only layout it shipped with before F7.
  const slot =
    previewStep !== null && typeof previewStep.action_slot === "string"
      ? previewStep.action_slot
      : null;
  const toyId =
    activity.toy_ids !== undefined && activity.toy_ids.length > 0
      ? (activity.toy_ids[0] ?? null)
      : null;
  const showSprite = slot !== null && toyId !== null;
  const toyDisplayName =
    showSprite && toyId !== null ? lookupToyDisplayName(activity, toyId) : undefined;

  return (
    <section
      data-testid="step-card"
      data-step-seq={previewStep?.seq ?? 0}
      data-current-index={currentIndex}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        gap: 24,
        maxWidth: 1100,
        padding: "0 24px",
      }}
    >
      {totalSteps > 0 && (
        <div
          style={{
            color: "#777",
            fontSize: 22,
            letterSpacing: 1,
            textTransform: "uppercase",
          }}
        >
          {currentIndex > 0 ? `Step ${currentIndex} of ${totalSteps}` : "Ready"}
        </div>
      )}
      <div
        data-testid="step-body-row"
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          // Gap between sprite and body text when the sprite renders;
          // collapses harmlessly to zero visual when the sprite is
          // absent (single-child flex row).
          gap: 24,
          width: "100%",
        }}
      >
        {showSprite && slot !== null && toyId !== null && (
          <ToyActionSprite
            toyId={toyId}
            slot={slot}
            toyDisplayName={toyDisplayName}
          />
        )}
        <h1
          data-testid="step-text"
          style={{
            margin: 0,
            // ``flex: 1`` lets the body text fill the remaining row
            // width when the sprite is present, and naturally take
            // the full row when it isn't.
            flex: 1,
            fontSize: "clamp(2rem, 5vw, 4rem)",
            lineHeight: 1.15,
            fontWeight: 700,
            color: "#222",
          }}
        >
          {previewStep?.body ?? activity.title ?? "Get ready..."}
        </h1>
      </div>
    </section>
  );
}
