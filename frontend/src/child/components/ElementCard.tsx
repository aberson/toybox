// Phase M Step M3 — Kiosk Periodic Table element card.
//
// Renders inline above the step body when the runtime ``ActivityStep``
// carries a non-null ``element_id``. Wire shape (denormalized at
// serialize time in ``api/activities.py`` ``_enrich_element_metadata``)
// supplies ``element_id`` as a top-level field AND mirrors
// ``element_id`` / ``element_symbol`` / ``element_name`` /
// ``element_atomic_number`` into ``step.metadata`` so the kiosk can
// render without a separate ``/api/elements/<id>`` fetch — same pattern
// as song's ``metadata.audio_url`` and joke's ``metadata.punchline``
// (K13).
//
// Sprite source. The static mount lives at ``/api/static/elements/`` —
// wired in ``app.py`` against the ``elements_root()`` helper in
// ``activities/element_corpus.py``. Sprite bytes are produced by
// ``scripts/generate_element_sprites.py`` (M2a; soak deferred alongside
// M14) and committed under ``data/images/elements/<element_id>.png``.
// When a sprite is missing (pre-soak runs, or a future element added
// after sprite generation), the static mount 404s and the ``<img>``
// element fires ``onError`` — we swap the src to a bundled periodic-
// table avatar (imported as a Vite asset, emitted with a hashed URL
// at build time) so the surface degrades gracefully instead of
// rendering a broken-image glyph. Iter-1 reviewers flagged that the
// previous fallback URL (``/api/static/personas/library/avatars/
// periodic_table.png``) had no backing static mount in ``app.py`` and
// would always 404; option B (Vite asset import) is the minimal fix
// per the iter-2 prompt — no new backend mount surface, no persona-
// avatar coupling. Asset bytes live next to this file at
// ``frontend/src/child/assets/periodic_table_fallback.png``.
//
// Visual spec from documentation/phase-m-plan.md §5.3:
//   - Sprite 256×256 (responsive: clamps down on narrow viewports).
//   - Element symbol large (~120pt equivalent via clamp()).
//   - Element name medium (~36pt).
//   - Atomic number small (~24pt).
//   - Rounded card, soft drop-shadow, pulses gently for ~1s on mount.

import { useState, type JSX } from "react";

import periodicTableFallbackUrl from "../assets/periodic_table_fallback.png";

export interface ElementCardProps {
  /** Composite id `<symbol-lower>-<atomic_number>` (e.g. `au-79`). */
  elementId: string;
  /** Display-case element symbol (e.g. `Au`). 1-3 chars. */
  symbol: string;
  /** Common name (e.g. `Gold`). */
  name: string;
  /** Atomic number (1-118 inclusive). */
  atomicNumber: number;
}

/** Bundled fallback when an element sprite 404s. Imported as a Vite
 * asset (resolves to a hashed URL at build time, a same-process file
 * URL at vitest runtime) so no backend static mount has to exist for
 * the fallback to load. The periodic-table avatar is the persona
 * avatar shared by every element-bearing template (per phase-m-plan
 * §6.9 there's no per-element persona gating). */
const PERIODIC_TABLE_FALLBACK_SRC: string = periodicTableFallbackUrl;

function buildSpriteSrc(elementId: string): string {
  // Defensive: refuse anything that looks like an absolute URL or
  // path-traversal payload. The backend Pydantic+jsonschema gates
  // ``element_id`` to ``^[a-z]{1,3}-[0-9]{1,3}$`` already; this
  // belt-and-braces keeps a malformed envelope from injecting an URL
  // escape into the rendered ``<img src>``.
  if (!/^[a-z]{1,3}-[0-9]{1,3}$/.test(elementId)) {
    return PERIODIC_TABLE_FALLBACK_SRC;
  }
  return `/api/static/elements/${elementId}.png`;
}

// Inline component-scoped styles in a ``<style>`` block, matching the
// kiosk convention used by ``ReadMeButton.tsx``, ``ClickableText.tsx``,
// and ``StepCard.tsx``. Iter-1 shipped a standalone ``ElementCard.css``;
// reviewers flagged the convention break — kiosk components keep their
// CSS co-located so a future bundle-split or theme-swap pass has one
// file to touch per component. The @keyframes block is small (3 stops
// + a reduced-motion override) so it stays inline rather than moving
// to ``animations/`` (which is reserved for the large reward animation
// keyframe set per the ``rewardAnimations.css`` precedent).
const ELEMENT_CARD_STYLES = `
.kiosk-element-card {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 24px;
  padding: 20px 28px;
  background: #ffffff;
  border-radius: 24px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
  animation: kiosk-element-card-pulse 1000ms ease-out forwards;
  max-width: min(720px, 92vw);
}
.kiosk-element-card-sprite {
  width: clamp(128px, 28vw, 256px);
  height: clamp(128px, 28vw, 256px);
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: 16px;
  background: #f5f5f5;
}
.kiosk-element-card-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 4px;
  text-align: left;
}
.kiosk-element-card-symbol {
  font-size: clamp(64px, 16vw, 160px);
  font-weight: 800;
  line-height: 1;
  color: #1a1a1a;
  letter-spacing: -2px;
}
.kiosk-element-card-name {
  font-size: clamp(24px, 5vw, 48px);
  font-weight: 600;
  line-height: 1.1;
  color: #333;
}
.kiosk-element-card-atomic-number {
  font-size: clamp(18px, 3.5vw, 32px);
  font-weight: 500;
  line-height: 1.1;
  color: #777;
}
@keyframes kiosk-element-card-pulse {
  0%   { transform: scale(0.95); opacity: 0.4; }
  60%  { transform: scale(1.03); opacity: 1; }
  100% { transform: scale(1);    opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .kiosk-element-card { animation: none; }
}
`;

export function ElementCard(props: ElementCardProps): JSX.Element {
  const { elementId, symbol, name, atomicNumber } = props;
  // ``imgSrc`` starts at the per-element sprite path and swaps to the
  // fallback exactly once on the first 404. Tracking ``didFallback``
  // prevents a fallback-image 404 (e.g. operator deleted the bundled
  // periodic-table avatar) from triggering an infinite onError loop.
  const initialSrc = buildSpriteSrc(elementId);
  const [imgSrc, setImgSrc] = useState<string>(initialSrc);
  const [didFallback, setDidFallback] = useState<boolean>(false);

  const handleImageError = (): void => {
    if (didFallback) return;
    setDidFallback(true);
    setImgSrc(PERIODIC_TABLE_FALLBACK_SRC);
  };

  return (
    <>
      <style>{ELEMENT_CARD_STYLES}</style>
      <div
        data-testid="element-card"
        data-element-id={elementId}
        className="kiosk-element-card"
      >
        <img
          data-testid="element-card-sprite"
          className="kiosk-element-card-sprite"
          src={imgSrc}
          alt={`${name} sprite`}
          onError={handleImageError}
          // ``width``/``height`` attrs hint the browser to reserve box
          // size during decode so the layout doesn't jump on slow loads.
          // CSS clamps the rendered size for narrow viewports.
          width={256}
          height={256}
        />
        <div className="kiosk-element-card-text">
          <div
            data-testid="element-card-symbol"
            className="kiosk-element-card-symbol"
          >
            {symbol}
          </div>
          <div
            data-testid="element-card-name"
            className="kiosk-element-card-name"
          >
            {name}
          </div>
          <div
            data-testid="element-card-atomic-number"
            className="kiosk-element-card-atomic-number"
          >
            #{atomicNumber}
          </div>
        </div>
      </div>
    </>
  );
}
