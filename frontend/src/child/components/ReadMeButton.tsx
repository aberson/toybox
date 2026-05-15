// Phase K Step K9 — Read Me button.
//
// Watermarked "?" bubble positioned by the parent step-card container.
// Tapping speaks the full step body via the K8 TTS substrate. Render is
// flag-gated: when ``enabled`` is false the component returns ``null``
// so an absent flag adds NO DOM nodes (the kiosk is hot-path for the
// React reconciler; a null return is the cheapest "off").
//
// Positioning contract: the consumer (``StepCard``) sets
// ``position: relative`` on its container; this component renders
// ``position: absolute`` with ``bottom`` + ``left`` so the watermark
// pins to the bottom-left of the visible step card regardless of the
// body text's length. Keeping the positioning rule inside the component
// (rather than asking each call site to wrap it in a positioned div)
// means a future StepCard refactor can't accidentally drop the
// affordance — the button is self-positioning by contract.
//
// Hit target: 48×48px (≥44pt per Apple HIG, with breathing room for
// the watermark's reduced opacity making the boundary less obvious to
// a quick tap).

import type { CSSProperties, JSX } from "react";

import { cancel, speak, type VoiceProfile } from "../tts";

export interface ReadMeButtonProps {
  text: string;
  profile: VoiceProfile;
  enabled: boolean;
}

// Hit target ≥44pt per Apple HIG. Inflated to 48px so the bubble's
// reduced baseline opacity (0.6) doesn't make the actual tap area feel
// smaller than the visible glyph implies.
const HIT_TARGET_PX = 48;

// Watermark baseline opacity. Matches the plan §6 K9 callout (~0.6).
// On hover / focus / active we flip to full opacity for the visible
// affordance feedback. Tab-reachability is provided by the native
// ``<button>`` element with no ``tabIndex={-1}``.
const BASE_OPACITY = 0.6;

export function ReadMeButton(props: ReadMeButtonProps): JSX.Element | null {
  const { text, profile, enabled } = props;
  if (!enabled) return null;

  const handleClick = (): void => {
    // Interrupt any in-flight speech (e.g. the kid hit ReadMe twice in
    // succession, or a word tap from ClickableText is mid-utterance)
    // so the read-me starts cleanly from the beginning.
    cancel();
    // Swallow rejections — see ClickableText for the rationale.
    void speak(text, profile).catch(() => {});
  };

  // Hover / focus / active styling is hard to express inline (React
  // can't subscribe to those pseudo-states) so we use a class + a
  // scoped <style> block. Same pattern as ClickableText.
  return (
    <>
      <style>{`
        .kiosk-read-me-button {
          opacity: ${BASE_OPACITY};
          transition: opacity 120ms ease-out;
        }
        .kiosk-read-me-button:hover,
        .kiosk-read-me-button:focus,
        .kiosk-read-me-button:active {
          opacity: 1;
        }
      `}</style>
      <button
        type="button"
        data-testid="read-me-button"
        className="kiosk-read-me-button"
        aria-label="Read Me"
        onClick={handleClick}
        style={ABSOLUTE_BOTTOM_LEFT_STYLE}
      >
        ?
      </button>
    </>
  );
}

// Extracted as a module-level constant so React's render pass doesn't
// allocate a new object every paint (the kiosk renders this on every
// StepCard re-render). The opacity transition lives in the CSS block
// above so the inline style stays static.
const ABSOLUTE_BOTTOM_LEFT_STYLE: CSSProperties = {
  position: "absolute",
  bottom: 16,
  left: 16,
  width: HIT_TARGET_PX,
  height: HIT_TARGET_PX,
  minWidth: HIT_TARGET_PX,
  minHeight: HIT_TARGET_PX,
  borderRadius: "50%",
  border: "2px solid #1976d2",
  background: "white",
  color: "#1976d2",
  fontSize: 28,
  fontWeight: 700,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
};
