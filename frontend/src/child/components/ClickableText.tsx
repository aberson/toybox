// Phase K Step K9 — word-level click-to-read.
//
// Wraps text in word-level ``<span>``s. Tapping a word interrupts any
// in-flight speech and speaks the single word using the supplied
// persona ``VoiceProfile``. When ``enabled`` is false the component
// renders the text plain (single ``<span>``) so the flag toggle is a
// straight render swap rather than a CSS-driven hide — the K8 TTS
// substrate never gets touched in the disabled path.
//
// Whitespace preservation: ``text.split(/\s+/)`` would collapse runs of
// whitespace and lose any specific separator the parent picked (e.g. a
// non-breaking space inside a step body). Instead we tokenize with the
// matchAll regex pattern below — alternating runs of ``\S+`` and ``\s+``
// — and emit each in its source order. Word tokens become tap targets;
// whitespace tokens render as plain text spans so the rendered string
// reads identically to the unsplit input.
//
// stopPropagation on the word click is intentional: the only place
// ClickableText is mounted inside a clickable parent today is
// ``ChoiceButton`` (whose ``<button>`` wraps the label). Without the
// stopPropagation, a word tap inside a choice would also fire the
// choice's submit handler and immediately advance the activity —
// opposite of the read-aloud affordance. We always call
// stopPropagation; the StepCard main-text mount has no clickable
// ancestor so the call is harmless there.

import { useState, type JSX, type MouseEvent } from "react";

import { cancel, speak, type VoiceProfile } from "../tts";

export interface ClickableTextProps {
  text: string;
  profile: VoiceProfile;
  enabled: boolean;
}

interface Token {
  // ``true`` = a run of non-whitespace characters; rendered as a
  // tappable word span. ``false`` = a run of whitespace; rendered as a
  // plain text span so the line break / multi-space input survives.
  word: boolean;
  value: string;
}

function tokenize(text: string): Token[] {
  // The empty string degenerates to zero tokens — caller gets a single
  // empty span. Avoids a useless allocation in the common empty path.
  if (text === "") return [];
  const tokens: Token[] = [];
  // Alternate \S+ (word) and \s+ (separator) so the rendered output is
  // byte-identical to the input when concatenated. ``matchAll`` returns
  // each match in source order — we don't need to track position
  // ourselves.
  const re = /(\S+)|(\s+)/g;
  for (const m of text.matchAll(re)) {
    if (m[1] !== undefined) {
      tokens.push({ word: true, value: m[1] });
    } else if (m[2] !== undefined) {
      tokens.push({ word: false, value: m[2] });
    }
  }
  return tokens;
}

// Brief outline window (ms). Matches the plan's "~200ms outline on tap"
// hint. Long enough to register as a visible flash even for a tap that
// scrolls past quickly; short enough not to linger past the kid's next
// tap. The outline state is per-word-index so a rapid two-word tap
// flashes both independently.
const OUTLINE_MS = 200;

export function ClickableText(props: ClickableTextProps): JSX.Element {
  const { text, profile, enabled } = props;
  // ``flashedIndex`` is the word-token index currently rendering its
  // tap outline (or null when none). One slot is enough — a kid's
  // single-finger tap is sequential, and overlapping flashes from a
  // truly parallel input device would be a kiosk-level UX concern
  // outside K9's scope.
  const [flashedIndex, setFlashedIndex] = useState<number | null>(null);

  if (!enabled) {
    return (
      <span data-testid="clickable-text" data-clickable="false">
        {text}
      </span>
    );
  }

  const tokens = tokenize(text);

  const handleWordClick = (e: MouseEvent, index: number, word: string): void => {
    // Always stopPropagation — see file header for why this is safe at
    // the StepCard mount and required at the ChoiceButton mount.
    e.stopPropagation();
    setFlashedIndex(index);
    // Schedule the flash clear. We deliberately do NOT cancel a prior
    // flash timer: a fresh setFlashedIndex overrides the displayed
    // index, and the older setTimeout will null out an already-null
    // slot when it fires (no-op). Saves a ref and a clearTimeout call
    // for the kiosk's most common interaction.
    setTimeout(() => {
      setFlashedIndex((prev) => (prev === index ? null : prev));
    }, OUTLINE_MS);
    // Interrupt any in-flight speech (e.g. a previous word still being
    // read) so the new word's audio is immediate. ``cancel`` is itself
    // idempotent — safe to call when nothing is speaking.
    cancel();
    // Swallow rejections — the K8 substrate rejects on engine errors
    // (or after our own cancel() fires), and the kiosk's UX is "tap a
    // word, hear it OR hear nothing if the engine balked." A thrown
    // promise from an event handler would surface as an unhandled
    // rejection in the console; we explicitly catch.
    void speak(word, profile).catch(() => {});
  };

  return (
    <>
      {/*
        Hover underline lives in a CSS rule because React can't subscribe
        to ``:hover``. Inline because the kiosk has no global stylesheet
        (see ``index.html`` and ``main.tsx``'s no-CSS-import precedent).
        Scoped via the ``.kiosk-word`` class so it can't bleed into
        unrelated text. Lives OUTSIDE the wrapper span so its rule text
        does NOT contaminate ``wrapper.textContent`` for testid lookups
        (the ``<style>`` element has its own textContent that browsers
        treat as CSS source, not visible text).
      */}
      <style>{`.kiosk-word:hover { text-decoration: underline; }`}</style>
      <span data-testid="clickable-text" data-clickable="true">
      {tokens.map((tok, i) => {
        if (!tok.word) {
          // Whitespace token — render as a plain span so the visible
          // text layout matches the unsplit input exactly. No key
          // collision risk because indexes are stable across renders
          // (the source string doesn't reorder).
          return (
            <span key={`ws-${i}`} data-ws="true">
              {tok.value}
            </span>
          );
        }
        const isFlashed = flashedIndex === i;
        return (
          <span
            key={`w-${i}`}
            className="kiosk-word"
            data-testid="clickable-word"
            data-word-index={i}
            // role/tabIndex omitted on purpose: the K9 plan calls out
            // the Read Me button as the keyboard-reachable affordance.
            // Word-level focus would explode tab order for a kiosk
            // body of arbitrary length. Touch-only on the words.
            onClick={(e) => handleWordClick(e, i, tok.value)}
            style={{
              cursor: "pointer",
              // CSS hover underline (the styled-jsx alternative would
              // need a CSS module / Tailwind, neither of which the
              // kiosk uses elsewhere — see ChoiceButton's inline-style
              // precedent). The :hover decoration is applied via
              // ``textDecoration`` toggled only by the browser; React
              // can't observe :hover, so we set it always at the CSS
              // layer via a className + a tiny inline rule below.
              // The brief tap outline is driven by ``isFlashed`` from
              // state — a 200ms timer (above) clears it.
              outline: isFlashed ? "2px solid #1976d2" : "none",
              outlineOffset: 2,
              borderRadius: 4,
              transition: "outline 80ms ease-out",
            }}
          >
            {tok.value}
          </span>
        );
      })}
      </span>
    </>
  );
}
