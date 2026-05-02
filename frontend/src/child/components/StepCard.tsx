import type { JSX } from "react";

import type { Activity } from "../api";

export interface StepCardProps {
  activity: Activity;
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
      <h1
        data-testid="step-text"
        style={{
          margin: 0,
          fontSize: "clamp(2rem, 5vw, 4rem)",
          lineHeight: 1.15,
          fontWeight: 700,
          color: "#222",
        }}
      >
        {previewStep?.body ?? activity.title ?? "Get ready..."}
      </h1>
    </section>
  );
}
