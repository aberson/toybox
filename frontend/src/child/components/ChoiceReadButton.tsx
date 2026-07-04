// Per-option read-aloud bubble (split from the single K9 Read Me
// button per operator request: "one for the prompt and one for each
// option").
//
// One instance renders NEXT TO each ChoiceButton on choice-bearing
// steps. Tapping speaks that option's label via the K8 TTS substrate —
// nothing else: the bubble is a SIBLING of the ChoiceButton, never a
// descendant (a <button> may not nest inside a <button>, and a shared
// tap surface would risk a read-aloud tap advancing the activity).
//
// Render is flag-gated by the SAME ``read_me_button_enabled`` household
// flag as the prompt's Read Me bubble: when ``enabled`` is false the
// component returns ``null`` so an absent flag adds NO DOM nodes
// (kiosk hot-path convention — see ReadMeButton).
//
// Visual language matches the K9 watermark ("?" in a blue-bordered
// white circle at 0.6 baseline opacity) so the affordance the kid
// already knows — "the ? reads text to me" — carries over; adjacency
// to the option pill communicates WHAT gets read. Unlike ReadMeButton
// this bubble is laid out inline (flex child of the choice row), not
// ``position: fixed`` — it must travel with its option.
//
// Hit target: 44×44px (≥44pt per Apple HIG). Slightly smaller than the
// prompt bubble's 48px so a 4-choice stack on iPad portrait doesn't
// grow taller than the pre-split layout.

import type { CSSProperties, JSX } from "react";

import {
  effectiveClipUrl,
  isClipInterrupted,
  playClip,
  stopClip,
} from "../clip-audio";
import { cancel, speak, type VoiceProfile } from "../tts";
import { truncateSpokenText } from "./ReadMeButton";

export interface ChoiceReadButtonProps {
  label: string;
  choiceIndex: number;
  profile: VoiceProfile;
  enabled: boolean;
  // Parent-configured spoken text limit (Phase R R2; sentence-aware
  // since Phase Z Z2) — applied to the label before ``speak()`` exactly
  // like ReadMeButton applies it to the step body. ``0`` (or omitted)
  // means no truncation. Phase Z Z5: fallback path only — the neural
  // clip always speaks the full label.
  limit?: number;
  // Phase Z Z5: server-rendered neural clip for THIS choice's label —
  // the ``choiceIndex``-aligned entry of the step's
  // ``metadata.spoken_choice_audio_urls`` (StepCard does the index
  // lookup so this component stays index-blind, same as ``label``).
  // Failure/absence falls back to the Web Speech path above.
  clipUrl?: string | null;
  // Phase Z Z5: neural-voice gate — defaults TRUE; Z6 wires the
  // ``neural_voice_enabled`` parent flag. Off → no clip attempts.
  neuralVoiceEnabled?: boolean;
}

const HIT_TARGET_PX = 44;

// Watermark baseline opacity — matches ReadMeButton's BASE_OPACITY so
// the two read-aloud affordances read as one family.
const BASE_OPACITY = 0.6;

export function ChoiceReadButton(
  props: ChoiceReadButtonProps,
): JSX.Element | null {
  const {
    label,
    choiceIndex,
    profile,
    enabled,
    limit = 0,
    clipUrl = null,
    neuralVoiceEnabled = true,
  } = props;
  if (!enabled) return null;
  // A blank label has nothing to speak — mirror StepCard's "no body,
  // no Read Me" suppression rather than mounting a dead button.
  if (label.length === 0) return null;

  // Phase Z Z5: Web Speech path — pre-Z5 behavior, now also the clip
  // fallback. stopClip() keeps single audio focus (see ReadMeButton).
  const speakFallback = (): void => {
    stopClip();
    // Interrupt any in-flight speech (double-tap, a word tap from
    // ClickableText, or the prompt bubble mid-utterance) so this
    // option's read starts cleanly from the beginning.
    cancel();
    const spokenText = truncateSpokenText(label, limit);
    // Swallow rejections — see ClickableText for the rationale.
    void speak(spokenText, profile).catch(() => {});
  };

  const handleClick = (): void => {
    // Phase Z Z5: clip-first, mirroring ReadMeButton — full label on
    // the clip path (no truncation), Web Speech fallback on any clip
    // failure except interruption (another surface took focus).
    const effective = effectiveClipUrl(neuralVoiceEnabled, clipUrl);
    if (effective !== null) {
      void playClip(effective).catch((err: unknown) => {
        if (isClipInterrupted(err)) return;
        speakFallback();
      });
      return;
    }
    speakFallback();
  };

  // Hover / focus / active opacity flip via a scoped class — same
  // pattern as ReadMeButton (React can't subscribe to pseudo-states
  // from inline styles).
  return (
    <>
      <style>{`
        .kiosk-choice-read-button {
          opacity: ${BASE_OPACITY};
          transition: opacity 120ms ease-out;
        }
        .kiosk-choice-read-button:hover,
        .kiosk-choice-read-button:focus,
        .kiosk-choice-read-button:active {
          opacity: 1;
        }
      `}</style>
      <button
        type="button"
        data-testid="choice-read-button"
        data-choice-index={choiceIndex}
        className="kiosk-choice-read-button"
        aria-label={`Read choice: ${label}`}
        onClick={handleClick}
        style={INLINE_BUBBLE_STYLE}
      >
        ?
      </button>
    </>
  );
}

// Module-level constant so the kiosk's per-render pass doesn't allocate
// a fresh style object for every choice row (same rationale as
// ReadMeButton's FIXED_BOTTOM_LEFT_STYLE). ``flexShrink: 0`` keeps the
// bubble's hit target intact when the sibling ChoiceButton's label
// pushes the row wide.
const INLINE_BUBBLE_STYLE: CSSProperties = {
  width: HIT_TARGET_PX,
  height: HIT_TARGET_PX,
  minWidth: HIT_TARGET_PX,
  minHeight: HIT_TARGET_PX,
  flexShrink: 0,
  borderRadius: "50%",
  border: "2px solid #1976d2",
  background: "white",
  color: "#1976d2",
  fontSize: 24,
  fontWeight: 700,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
};
