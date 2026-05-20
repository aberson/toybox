// Phase G Step G4 — kiosk-side choice button. One instance per entry
// in ``step.choices`` (see ``ActivityStep.choices`` in
// ``../api.ts``). On tap, posts ``{choice_index}`` to
// ``/api/activities/{id}/advance`` via the supplied ``onChoose``
// callback (App.tsx wires this through ``api.advance(...)`` +
// ``withConflictHandler``). The button manages its OWN local
// in-flight + error state so the parent doesn't have to thread a
// per-button busy/error map through; siblings can be disabled
// externally via the ``disabled`` prop while one is in flight, but
// the in-flight visual + error indicator on THIS button stay local.
//
// Error handling per Phase G plan §G4:
//   - 4xx (other than 409): ``onChoose`` rejects with a non-conflict
//     error → button re-enables, inline error indicator surfaces.
//   - 409 (``If-Match-Version`` mismatch): ``onChoose`` resolves with
//     ``"conflict"`` (the parent's withConflictHandler swallowed it
//     and triggered the activity refetch) → button re-enables, no
//     error indicator (the kiosk re-renders against fresh state).
//   - 5xx: same as 4xx — ``onChoose`` rejects, error indicator shows.
//
// UI verification (visual polish on iPad) is bundled into G6 per the
// autonomous-build operating mode; this component ships with code
// review only.

import { useRef, useState, type JSX } from "react";

import type { VoiceProfile } from "../tts";
import { ClickableText } from "./ClickableText";

// Result of a tap when the parent's ``onChoose`` resolves.
//   - ``"ok"``: advance succeeded (the activity moved on).
//   - ``"conflict"``: 409 was caught by ``withConflictHandler``; the
//     refetch fired and the parent re-rendered against fresh state.
// On non-409 errors the parent's ``onChoose`` REJECTS instead — the
// button renders its inline error indicator on that path.
export type ChoiceResult = "ok" | "conflict";

export interface ChoiceButtonProps {
  label: string;
  choiceIndex: number;
  // External disable — set to true while a sibling ChoiceButton is
  // in flight so the kid can't double-tap across two buttons. The
  // CHOSEN button manages its own ``busy`` independently and stays
  // disabled until ``onChoose`` settles.
  disabled?: boolean;
  // Performs the advance POST. The parent owns version-conflict
  // handling and refetching — see ``withConflictHandler`` in
  // ``../api.ts``. Resolves with ``"ok"`` on success or
  // ``"conflict"`` on 409 (after refetch). Rejects on non-409 errors
  // so the button can show an inline error indicator.
  onChoose: (choiceIndex: number) => Promise<ChoiceResult>;
  // Phase K K9: persona voice profile + clickable-words flag drilled
  // through StepCard. When ``clickableWordsEnabled`` is true the
  // label is wrapped in ClickableText so word taps speak the word
  // (with stopPropagation so the choice's submit handler does NOT
  // fire). When false (or both props omitted), the button renders
  // identically to pre-K9 — preserves the F7/G4 layout fixtures.
  voiceProfile?: VoiceProfile;
  clickableWordsEnabled?: boolean;
}

export function ChoiceButton(props: ChoiceButtonProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [errored, setErrored] = useState(false);
  // Synchronous in-flight guard — set BEFORE the await so two
  // pointer events fired in the same frame (real-iPad double-tap
  // landing two clicks within one render) both see the latched flag
  // and only the first fires onChoose. ``busy`` (state) drives the
  // visual; ``busyRef`` (ref) is the latch the click handler reads
  // without waiting for a React commit. Belt-and-braces with App's
  // ``choosingRef`` — that ref handles the cross-button race; this
  // ref handles the same-button race.
  const busyRef = useRef(false);
  const externallyDisabled = props.disabled === true;
  const isDisabled = busy || externallyDisabled;

  const handleClick = (): void => {
    if (isDisabled) return;
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setErrored(false);
    void (async () => {
      try {
        await props.onChoose(props.choiceIndex);
        // Both "ok" and "conflict" are success-from-the-button's-POV;
        // the parent has already routed the result through the store.
      } catch {
        // Non-conflict failure (4xx / 5xx / network) — surface the
        // inline error indicator. The kid can re-tap; the button
        // re-enables on the next render because ``busy`` flips back
        // to false in the finally block. The error indicator clears
        // at the start of the next click (``setErrored(false)`` above).
        setErrored(true);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    })();
  };

  return (
    <button
      type="button"
      data-testid="choice-button"
      data-choice-index={props.choiceIndex}
      data-busy={busy ? "true" : "false"}
      data-errored={errored ? "true" : "false"}
      disabled={isDisabled}
      onClick={handleClick}
      style={{
        appearance: "none",
        border: errored ? "2px solid #d32f2f" : "none",
        borderRadius: 9999,
        // Touch target ≥44pt per Apple HIG; the vertical padding +
        // line height combine to ~56pt on iPad portrait (matches
        // NextStepButton's affordance). Vertical padding + fontSize
        // both shrink on short viewports so a 3-choice fork step keeps
        // every button + the body text + section margins on screen.
        padding: "clamp(10px, 1.8vh, 20px) clamp(20px, 4vw, 40px)",
        fontSize: "clamp(1rem, min(2.4vw, 2.8vh), 1.5rem)",
        fontWeight: 600,
        color: "white",
        background: isDisabled
          ? "#9aa0a6"
          : "linear-gradient(135deg, #1976d2 0%, #1565c0 100%)",
        boxShadow: "0 4px 14px rgba(25,118,210,0.30)",
        cursor: isDisabled ? "default" : "pointer",
        // Wide enough to feel like a button, capped so a 4-choice
        // stack on iPad portrait fits without scrolling.
        minWidth: 240,
        maxWidth: 600,
        width: "100%",
      }}
    >
      {busy ? (
        "..."
      ) : props.clickableWordsEnabled === true && props.voiceProfile !== undefined ? (
        // Phase K K9: word-level tap surface. Word ``onClick`` calls
        // ``stopPropagation`` so a word tap does NOT also fire this
        // button's onClick (which would advance the activity).
        <ClickableText
          text={props.label}
          profile={props.voiceProfile}
          enabled={true}
        />
      ) : (
        props.label
      )}
      {errored && (
        <span
          data-testid="choice-button-error"
          aria-label="error — try again"
          style={{
            display: "inline-block",
            marginLeft: 12,
            fontSize: "1rem",
            color: "#fff",
            background: "#d32f2f",
            borderRadius: 9999,
            padding: "2px 10px",
          }}
        >
          retry?
        </span>
      )}
    </button>
  );
}
