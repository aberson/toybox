// Phase F Step F7: small pixel-art sprite the kiosk renders next to
// a step's body when the step has an ``action_slot`` set AND the
// activity has at least one toy. The component is intentionally
// minimal — it is a passive ``<img>`` with an ``onError``-hides-element
// path so a 404 (capability disabled, generation not yet finished,
// generation failed) renders as "no sprite for this step" rather than
// a broken image. The kiosk's body text reads the same as today in
// that case.
//
// Mounting URL: ``/api/static/images/toy_actions/<toy_id>/<slot>.png``.
// The static-files mount lives at ``/api/static/images`` (see
// ``src/toybox/app.py``) and the worker writes sprites under the
// ``toy_actions/<toy_id>/<slot>.png`` subdirectory.
//
// Accessibility: the ``alt`` attribute is mandatory — toybox is
// family-facing and the F8 parent grid will reuse this component, so
// a11y is not optional. When ``toyDisplayName`` is provided the alt is
// ``"<display_name> <slot>"`` (e.g. "Mr. Unicorn looking"); otherwise
// it falls back to the bare slot key.

import { useState } from "react";
import type { CSSProperties, JSX } from "react";

export interface ToyActionSpriteProps {
  // UUID of the toy whose sprite to fetch. Combined with ``slot`` to
  // form the static-files URL. The component does NOT validate the
  // shape; bad input simply produces a 404 the ``onError`` path hides.
  toyId: string;
  // One of the 10 ACTION_SLOTS members (e.g. "looking", "jumping",
  // "idle"). Component is permissive about the value — any string is
  // accepted because the URL is the source of truth and a bad slot
  // surfaces as a 404 the ``onError`` handler hides.
  slot: string;
  // Optional toy display name for accessibility. When present the alt
  // is "<display_name> <slot>"; otherwise alt is the bare slot key.
  toyDisplayName?: string;
  // Pixel size for the rendered sprite. Defaults to 112 px — sits in
  // the plan's 96-128 px design band. The kiosk uses the default; the
  // F8 parent grid will pass a smaller value for the cell size.
  size?: number;
  // Optional style override merged onto the default. Lets parent
  // layouts adjust margin / flex behavior without re-deriving the
  // base pixel-art presentation rules.
  style?: CSSProperties;
  // Optional cache-bust query value. When set, the sprite URL becomes
  // ``<base>?v=<encodeURIComponent(cacheKey)>`` so a regenerated
  // on-disk PNG at the same path renders the new bytes instead of the
  // browser-cached bitmap. Used by the parent ToyActionGrid (threads
  // ``row.seed``); the kiosk omits it (no cache-bust needed there).
  cacheKey?: string;
}

export function ToyActionSprite(props: ToyActionSpriteProps): JSX.Element | null {
  const size = props.size ?? 112;
  // ``loaded`` starts true; the ``onError`` handler flips it to false
  // when the browser reports the fetch failed. Returning null after a
  // failure removes the element from the DOM entirely so the kiosk
  // body text reflows to fill the row width.
  const [loaded, setLoaded] = useState<boolean>(true);
  if (!loaded) return null;
  const alt =
    props.toyDisplayName !== undefined && props.toyDisplayName.length > 0
      ? `${props.toyDisplayName} ${props.slot}`
      : props.slot;
  const baseStyle: CSSProperties = {
    width: size,
    height: size,
    flexShrink: 0,
    // Phase P4 ships 512² source PNGs (was 128²); the browser's
    // default smooth resampling on downscale to 112 px display is
    // correct. No ``imageRendering: pixelated`` override here —
    // pixelated was a pixel-art crispness hint for upscaling the
    // old 128² source and would now make the 512→112 downscale
    // jagged.
    objectFit: "contain",
    // Transparent background — sprite PNGs have an alpha channel and
    // we want the kiosk's gradient to show through.
    background: "transparent",
  };
  const merged: CSSProperties = { ...baseStyle, ...(props.style ?? {}) };
  const baseUrl = `/api/static/images/toy_actions/${props.toyId}/${props.slot}.png`;
  const src =
    props.cacheKey !== undefined
      ? `${baseUrl}?v=${encodeURIComponent(props.cacheKey)}`
      : baseUrl;
  return (
    <img
      data-testid="toy-action-sprite"
      data-slot={props.slot}
      data-toy-id={props.toyId}
      src={src}
      alt={alt}
      width={size}
      height={size}
      onError={() => setLoaded(false)}
      style={merged}
    />
  );
}
