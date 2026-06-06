// Phase K Step K9 — Read Me button.
//
// Watermarked "?" bubble pinned to the kiosk viewport's bottom-left.
// Tapping speaks the full step body via the K8 TTS substrate. Render is
// flag-gated: when ``enabled`` is false the component returns ``null``
// so an absent flag adds NO DOM nodes (the kiosk is hot-path for the
// React reconciler; a null return is the cheapest "off").
//
// Positioning contract: ``position: fixed`` with ``bottom`` + ``left``
// anchors the watermark to the visible viewport, NOT to the parent
// StepCard section. The original K9 contract pinned to the section's
// bottom-left via ``position: absolute`` inside a ``position: relative``
// container — that worked on linear text/joke step cards where the
// section's height matched the viewport, but drifted to mid-screen on
// fork cards where the choice-button stack inflated the section's
// height (fix for #137; operator UAT 2026-05-16). ``fixed`` anchors to
// the viewport regardless of section height, so the affordance lands
// in the same on-screen location across every step kind.
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
  // Phase R Step R2: optional character limit for spoken text. When > 0,
  // the text is truncated at a word boundary at or below ``limit`` chars
  // and a ``…`` is appended before passing to ``speak()``. The full
  // ``text`` remains visible on screen — truncation only affects the TTS
  // call. ``0`` (or omitted) means no truncation.
  limit?: number;
}

// Truncate ``text`` to at most ``limit`` characters at a word boundary.
// Finds the last space at or before position ``limit`` and splits there.
// Returns ``text`` unchanged when:
//   - ``limit`` is 0 or falsy (off)
//   - ``text.length <= limit`` (already short enough)
// Appends ``…`` (U+2026) when truncation occurs.
function truncateAtWordBoundary(text: string, limit: number): string {
  if (!limit || text.length <= limit) return text;
  // Slice to the limit first, then walk back to the last space so we
  // don't cut mid-word. If no space is found (one very long word),
  // fall back to a hard cut at ``limit``.
  const slice = text.slice(0, limit);
  const lastSpace = slice.lastIndexOf(" ");
  const cutAt = lastSpace > 0 ? lastSpace : limit;
  return text.slice(0, cutAt) + "…";
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
  const { text, profile, enabled, limit = 0 } = props;
  if (!enabled) return null;

  const handleClick = (): void => {
    // Interrupt any in-flight speech (e.g. the kid hit ReadMe twice in
    // succession, or a word tap from ClickableText is mid-utterance)
    // so the read-me starts cleanly from the beginning.
    cancel();
    // Apply the spoken text limit before TTS. Truncation happens here
    // (not on the visible text) so the full body stays on screen.
    const spokenText = truncateAtWordBoundary(text, limit);
    // Swallow rejections — see ClickableText for the rationale.
    void speak(spokenText, profile).catch(() => {});
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
        style={FIXED_BOTTOM_LEFT_STYLE}
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
const FIXED_BOTTOM_LEFT_STYLE: CSSProperties = {
  position: "fixed",
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
