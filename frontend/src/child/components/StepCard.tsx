import type { JSX } from "react";

import type { Activity } from "../api";
import { getVoiceProfile, type PersonaMetadata } from "../persona-voice";
import { ChoiceButton, type ChoiceResult } from "./ChoiceButton";
import { ClickableText } from "./ClickableText";
import { NextStepButton } from "./NextStepButton";
import { ReadMeButton } from "./ReadMeButton";
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
  // Phase K K9: parent-controlled feature flags. Drilled through App →
  // StepCard so word-level taps and the Read Me button can be turned
  // off without redeploying. Optional + default false so existing
  // tests that mount StepCard without an App (e.g. F7's pure-layout
  // suite) don't have to thread no-op flags through — the kiosk's
  // production path always supplies real values from the bootstrap
  // fetch (see ``App.tsx``'s ``featureFlags`` state).
  clickableWordsEnabled?: boolean;
  readMeButtonEnabled?: boolean;
}

// Step kinds that should get a Read Me button. ``song`` is excluded —
// the audio surface is owned by K12's SongPlayer (a song step renders
// a bundled MP3, and a competing TTS read-aloud would interrupt the
// song). The wire schema for ``ActivityStep.kind`` is not yet declared
// on the ``ActivityStep`` interface (K12 lands the new kinds); for K9
// we read ``step.kind`` defensively from the wire envelope as a
// string and default to "text" when absent, matching the plan's
// "text is the implicit default kind" contract.
const READ_ME_ELIGIBLE_KINDS = new Set<string>(["text", "fork", "joke"]);

function resolvePersonaMetadata(activity: Activity): PersonaMetadata | null {
  // Defensive: ``activity.metadata`` is ``Record<string, unknown>`` on
  // the wire; the persona blob may be absent, malformed, or partially
  // hydrated. ``getVoiceProfile`` tolerates ``null`` and returns the
  // default profile, so any path here that can't produce a typed
  // PersonaMetadata returns ``null`` instead of throwing.
  const meta = activity.metadata as Record<string, unknown> | undefined;
  if (meta === undefined || meta === null) return null;
  const persona = meta["persona"];
  if (typeof persona !== "object" || persona === null) return null;
  return persona as PersonaMetadata;
}

function readStepKind(stepBody: unknown): string {
  // Phase K K12 is the source of truth for ``step.kind``. Until then,
  // the wire shape may include the field already (engine work in
  // flight) or omit it entirely. Read defensively so a missing field
  // collapses to the implicit ``text`` default and the Read Me button
  // mounts on the steps the K9 plan calls out.
  if (typeof stepBody === "object" && stepBody !== null) {
    const rec = stepBody as Record<string, unknown>;
    const kind = rec["kind"];
    if (typeof kind === "string" && kind.length > 0) return kind;
  }
  return "text";
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

  // Phase K K9: voice profile resolution + flag-driven affordances. The
  // resolver tolerates a null persona and returns the default profile;
  // ``ReadMeButton`` and ``ClickableText`` both render no-op when their
  // respective flag is false, so the kiosk's behavior collapses to
  // pre-K9 layout when both are off.
  const personaMeta = resolvePersonaMetadata(activity);
  const voiceProfile = getVoiceProfile(personaMeta);
  const clickableWordsEnabled = props.clickableWordsEnabled === true;
  const readMeButtonEnabled = props.readMeButtonEnabled === true;
  const stepKind = readStepKind(previewStep);
  // ``previewStep === null`` would surface as the "Get ready..." default
  // text — no actual step body to read aloud, so we suppress the Read
  // Me affordance until a step lands.
  const readMeText = previewStep?.body ?? "";
  const showReadMe =
    readMeButtonEnabled &&
    previewStep !== null &&
    readMeText.length > 0 &&
    READ_ME_ELIGIBLE_KINDS.has(stepKind);

  return (
    <section
      data-testid="step-card"
      data-step-seq={previewStep?.seq ?? 0}
      data-current-index={currentIndex}
      data-step-kind={stepKind}
      style={{
        // ``position: relative`` is the K9 positioning contract — the
        // ``ReadMeButton`` renders ``position: absolute`` and pins to
        // this container's bottom-left. Adding it unconditionally is
        // safe: the prior layout was static-positioned, so a relative
        // wrapper doesn't change child sizing.
        position: "relative",
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
          {/*
            Phase K K9: wrap the visible step body in ClickableText so
            word taps speak the tapped word. The fallback chain stays
            identical to pre-K9 (previewStep.body → activity.title →
            "Get ready..."). When ``clickableWordsEnabled`` is false
            ClickableText renders a plain ``<span>`` — the kiosk's
            visible H1 layout is byte-identical to pre-K9.
          */}
          <ClickableText
            text={previewStep?.body ?? activity.title ?? "Get ready..."}
            profile={voiceProfile}
            enabled={clickableWordsEnabled}
          />
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
              // Phase K K9: thread the voice profile + flag so the
              // label renders as ClickableText. ChoiceButton handles
              // the off-state by rendering the bare label string —
              // pre-K9 layout intact.
              voiceProfile={voiceProfile}
              clickableWordsEnabled={clickableWordsEnabled}
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
      {/*
        Phase K K9: watermarked Read Me bubble. Self-positioning via
        ``position: absolute`` inside this section's ``position:
        relative`` container (set above). Mounted only on text / fork /
        joke step kinds — song steps own the audio surface (K12). The
        component returns ``null`` when the flag is off, so an off
        flag adds zero DOM nodes.
      */}
      {showReadMe && (
        <ReadMeButton
          text={readMeText}
          profile={voiceProfile}
          enabled={readMeButtonEnabled}
        />
      )}
    </section>
  );
}
