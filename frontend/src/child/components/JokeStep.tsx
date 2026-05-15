// Phase K Step K12 — Kiosk joke step delivery.
//
// Renders a step of ``kind: "joke"`` as a two-beat setup/punchline. The
// setup appears immediately and is auto-spoken; after a 1.5s pause the
// punchline reveals and is auto-spoken too. Both lines are wrapped in
// ClickableText so word-level taps work, and the kiosk's K9
// ReadMeButton (mounted by StepCard, not here) re-plays BOTH lines on
// tap. K12 is kiosk-frontend-only — K13 wires the standalone-intent
// backend path that emits joke steps with ``setup`` (body) and
// ``punchline`` (metadata) populated; K14/K15 wire embedded + parent-
// insert + spontaneity sources of the same wire shape.
//
// Punchline source. The plan §K12 calls for the punchline to ride on
// ``step.metadata.punchline``. The backend ``build_interjection_step``
// helper (K14) is the producer; until it ships, this component reads
// defensively — when ``punchline`` is empty or missing, the second
// beat collapses (no reveal, no second TTS call) so a malformed wire
// payload renders as a setup-only step rather than crashing the kiosk.
// The re-play handler likewise gates on a non-empty punchline.
//
// Auto-play vs gesture unlock. The K8 TTS substrate's
// ``speak()`` no-ops outside a user-gesture stack on iOS Safari until
// the first in-gesture speak primes ``unlocked``. The kiosk's idle →
// approve → start sequence ALWAYS goes through the kid's "I'm Ready!"
// tap (Phase G) before any step renders, so by the time a joke step
// mounts the substrate is unlocked. We still call speak() — the
// substrate handles the no-op path silently.

import { useEffect, useRef, useState, type JSX } from "react";

import { cancel, speak, type VoiceProfile } from "../tts";
import { ClickableText } from "./ClickableText";

export interface JokeStepProps {
  // The setup line — comes from ``step.body`` after slot substitution.
  setup: string;
  // The punchline — comes from ``step.metadata.punchline``. May be
  // empty when the backend hasn't yet wired the field (K13/K14);
  // empty collapses the reveal beat and the re-play is setup-only.
  punchline: string;
  // Voice profile from the resolved persona (K8). Threaded by
  // StepCard so JokeStep doesn't need to reach into activity metadata.
  profile: VoiceProfile;
  // K9 click-to-read flag. When false, ClickableText renders the
  // setup/punchline as plain spans (no word taps); auto-play of the
  // full lines is unaffected.
  clickableWordsEnabled: boolean;
}

// Reveal delay between setup and punchline. Plan §K12: "after a 1.5s
// pause". Mirrors a stand-up comic's beat — long enough for the kid
// to think about it, short enough that they don't lose interest.
const PUNCHLINE_REVEAL_MS = 1500;

export function JokeStep(props: JokeStepProps): JSX.Element {
  const { setup, punchline, profile, clickableWordsEnabled } = props;
  // ``revealed`` flips to true after the setTimeout fires; the
  // punchline DOM (and its TTS call) is gated on this state so a
  // re-render mid-pause doesn't double-fire the speak() pipeline.
  const [revealed, setRevealed] = useState(false);
  // Track whether we've already fired the auto-play sequence so a
  // re-render (StrictMode double-effect, prop change to profile, etc.)
  // doesn't trigger a second auto-play. The ReadMeButton's re-play
  // path bypasses this guard intentionally — a tap is a user request,
  // not an auto-play.
  const autoplayedRef = useRef(false);

  useEffect(() => {
    // Guard the double-effect: StrictMode mounts twice in dev. We don't
    // want two concurrent setup speaks racing each other on the same
    // mount lifecycle.
    if (autoplayedRef.current) return;
    autoplayedRef.current = true;
    // Setup speaks immediately. Errors are swallowed — the kid still
    // SEES the text, so a missing engine isn't a kiosk-blocker.
    void speak(setup, profile).catch(() => {});
    // Schedule the reveal + punchline speak.
    const id = setTimeout(() => {
      setRevealed(true);
      if (punchline.length > 0) {
        // We deliberately do NOT cancel the setup — a fast reader on a
        // slow engine might still be mid-utterance, and the kid's
        // experience is "setup, beat, punchline" not "setup gets
        // truncated when the punchline arrives." Modern Web Speech
        // queues utterances naturally; if the setup is still speaking
        // the punchline plays right after.
        void speak(punchline, profile).catch(() => {});
      }
    }, PUNCHLINE_REVEAL_MS);
    return () => {
      // Effect cleanup runs on unmount AND on dep-change. We only have
      // [] deps so this fires on unmount. Clearing the timer prevents
      // a setRevealed call on an unmounted component (React warning).
      clearTimeout(id);
      // ``cancel()`` is itself idempotent and silent — if no speech is
      // in flight it's a no-op. Calling it on unmount makes sure a
      // mid-utterance kiosk that advances doesn't leak audio into the
      // next step's render.
      cancel();
    };
    // setup / punchline / profile are stable for a given mounted step
    // (StepCard keys on step.seq, so a new step is a new mount). We
    // intentionally fire the effect once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      data-testid="joke-step"
      data-revealed={revealed ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 24,
        textAlign: "center",
        maxWidth: 900,
      }}
    >
      <div
        data-testid="joke-setup"
        style={{
          fontSize: "clamp(2rem, 5vw, 4rem)",
          lineHeight: 1.2,
          fontWeight: 700,
          color: "#222",
        }}
      >
        <ClickableText
          text={setup}
          profile={profile}
          enabled={clickableWordsEnabled}
        />
      </div>
      {/*
        Punchline reveal. We gate on ``revealed`` AND a non-empty
        punchline — the latter so a backend that hasn't yet wired the
        field (K13/K14) renders a setup-only joke rather than an empty
        bubble. Once K14's ``build_interjection_step`` ships the field
        consistently, this defensive empty-check becomes dead-but-
        harmless code that a future cleanup pass can remove.
      */}
      {revealed && punchline.length > 0 && (
        <div
          data-testid="joke-punchline"
          style={{
            fontSize: "clamp(1.5rem, 4vw, 3rem)",
            lineHeight: 1.2,
            fontWeight: 600,
            color: "#1976d2",
            // Slight indent on the punchline reads as a "second beat"
            // visually — the kid sees the structure even if they can't
            // read fluently yet.
            marginTop: 8,
          }}
        >
          <ClickableText
            text={punchline}
            profile={profile}
            enabled={clickableWordsEnabled}
          />
        </div>
      )}
    </div>
  );
}

/**
 * Re-play both lines (setup + punchline if present). Exposed as a
 * standalone helper because the K9 ReadMeButton's contract is
 * "speak this text" and the joke step has TWO lines, not one. The
 * StepCard mounts a customized ReadMeButton-equivalent click handler
 * that calls this helper when the kid taps the watermark on a joke
 * step. Living outside the component because it has no React state —
 * a pure side-effect on the TTS substrate.
 */
export function replayJoke(
  setup: string,
  punchline: string,
  profile: VoiceProfile,
): void {
  // Interrupt anything in flight (the user just tapped Read Me — they
  // want a fresh start, not a queue).
  cancel();
  // Speak both back-to-back. Web Speech queues, so they play in order.
  void speak(setup, profile).catch(() => {});
  if (punchline.length > 0) {
    void speak(punchline, profile).catch(() => {});
  }
}
