import type { JSX } from "react";

import type { Activity } from "../api";
import { ChoiceButton, type ChoiceResult } from "./ChoiceButton";
import { NextStepButton } from "./NextStepButton";
import { ToyActionSprite } from "./ToyActionSprite";

export interface StepCardProps {
  activity: Activity;
  // Phase G G4: action callbacks for the bottom button row. The
  // STEP-CARD picks which one to render based on whether the current
  // step is a branch point; the parent (``App.tsx``) supplies both
  // and threads version-conflict / store updates the same way for
  // either path. Optional so existing tests that mount StepCard for
  // pure layout assertions don't have to thread no-op callbacks
  // through; when omitted, the action row is hidden (matches the
  // previous behavior of rendering the avatar + body only).
  onAdvance?: () => void;
  onChoose?: (choiceIndex: number) => Promise<ChoiceResult>;
  // Linear-advance busy flag — the App's existing ``busyAdvance``
  // state. Choice-button busy is local to each ChoiceButton, but the
  // linear NextStepButton needs the parent's flag to disable while a
  // POST is in flight.
  advanceBusy?: boolean;
  // Phase G G4: index of the ChoiceButton that currently has a POST
  // in flight, or null if none. App lifts this state so EVERY rendered
  // ChoiceButton (including the in-flight one) gates its click handler
  // on it — preventing the sibling-tap race where a kid taps "Choice
  // A" then "Choice B" and both fire competing POSTs (the first wins
  // on version, the second 409s and the first becomes the kid's
  // "final" answer, opposite of UX expectation). Optional so existing
  // tests that mount StepCard without an App don't have to thread it.
  choosingIndex?: number | null;
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
  // Phase G G4: branching step? If the current step has a non-empty
  // ``choices`` list (rendered by the backend serializer from
  // ``activity_steps.choices_json`` per the plan §"How choices reach
  // the kiosk"), we render one button per choice instead of the bare
  // NextStepButton. The check guards against both ``undefined`` (the
  // pre-G3 wire shape that omits the field) and ``null`` (the G3 wire
  // shape on linear steps).
  const choices =
    currentStep !== null &&
    Array.isArray(currentStep.choices) &&
    currentStep.choices.length > 0
      ? currentStep.choices
      : null;

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
          {/*
            Phase G G4: drop the "of N" denominator. Phase G makes the
            step count variable per template (3-20 nodes, branching
            paths that visit different subsets) — a literal "Step 3 of
            7" implies a fixed total that the kid won't actually
            traverse on a branched playthrough. The bare "Step N"
            keeps the progress hint without lying about the destination.
          */}
          {currentIndex > 0 ? `Step ${currentIndex}` : "Ready"}
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
      {/*
        Phase G G4: action button row. When the current step has
        ``choices`` we render a vertical stack of ChoiceButtons (one
        per branch); otherwise the existing NextStepButton. The
        callbacks are optional so layout-only tests that mount
        StepCard without an App don't have to thread no-ops; an
        omitted callback hides the button (the kiosk always supplies
        them in production via App.tsx).
      */}
      {choices !== null && props.onChoose !== undefined && (
        <div
          data-testid="choice-button-stack"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 16,
            width: "100%",
            maxWidth: 600,
            marginTop: 24,
          }}
        >
          {choices.map((choice) => (
            <ChoiceButton
              key={choice.choice_index}
              label={choice.label}
              choiceIndex={choice.choice_index}
              onChoose={props.onChoose!}
              // Disable EVERY button (including the in-flight one) when
              // any choice is being processed. The in-flight button's
              // own internal ``busy`` already disables it locally, but
              // gating it via the App-level prop too is defensive and
              // means siblings + self share one disable signal — no
              // gap between the two for a tap to slip through.
              disabled={
                props.choosingIndex !== undefined &&
                props.choosingIndex !== null
              }
            />
          ))}
        </div>
      )}
      {choices === null && props.onAdvance !== undefined && (
        <NextStepButton
          onClick={props.onAdvance}
          busy={props.advanceBusy === true}
        />
      )}
    </section>
  );
}
