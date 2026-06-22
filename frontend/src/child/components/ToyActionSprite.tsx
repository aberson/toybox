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
// ``src/toybox/app.py``).
//
// Phase V shipped a steady-state ``.webp`` swap for the idle slot (an
// SVD-generated animated sprite). That pipeline produced unusable,
// garbled output for every toy, so the swap is removed: the kiosk now
// renders the static ``.png`` for every slot. The idle slot keeps its
// CSS slot-entry intro animation (the ``data-animating`` state machine
// below) but no longer swaps to an animated raster.
//
// "Claude Images": when the parent flag is on, the backend writes a
// Claude-authored ``<slot>.svg`` (idle self-animating) instead of the
// PNG. The kiosk passes ``preferSvg`` so this component tries ``.svg``
// first and falls back to ``.png`` (then hides) via the ``onError``
// chain. With the flag off, ``preferSvg`` is false and we load ``.png``
// directly — no wasted ``.svg`` 404 for the common case.
//
// Accessibility: the ``alt`` attribute is mandatory — toybox is
// family-facing and the F8 parent grid reuses this component, so
// a11y is not optional. When ``toyDisplayName`` is provided the alt is
// ``"<display_name> <slot>"`` (e.g. "Mr. Unicorn looking"); otherwise
// it falls back to the bare slot key.

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, JSX } from "react";

// CSS module import — Vite transforms CSS modules natively; data-animating
// attribute selectors in the .module.css file target the img element without
// class-name mangling. The import below ensures Vite processes the file during
// both dev and test runs (vitest uses Vite transforms).
import "./ToyActionSprite.module.css";

// Map of slot → CSS animation name (informational; actual animation targeting
// is done via the data-animating attribute in the CSS module above).
export const SLOT_INTRO_ANIMATIONS: Record<string, string> = {
  idle: "fadeIn",
  pointing: "slideInLeft",
  looking: "tiltIn",
  jumping: "bounceUp",
  cheering: "bounceWiggle",
  thinking: "floatIn",
  waving: "swingIn",
  running: "slideInFast",
  sleeping: "slowFadeIn",
  confused: "wobbleIn",
};

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
  // F8 parent grid passes a smaller value for the cell size.
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
  // "Claude Images" flag passthrough. When true, try ``<slot>.svg``
  // first (the Claude-authored vector sprite, idle self-animating) and
  // fall back to ``<slot>.png`` on a 404. Default false → load ``.png``
  // directly so the common (flag-off) path never pays a wasted ``.svg``
  // 404. The kiosk threads the household flag; the parent grid derives
  // it from each row's ``image_path`` extension.
  preferSvg?: boolean;
}

// Ordered on-disk formats to try, by ``preferSvg``. The ``onError`` chain
// advances through this list and hides the element once exhausted.
const SVG_FIRST: readonly string[] = ["svg", "png"];
const PNG_ONLY: readonly string[] = ["png"];

export function ToyActionSprite(props: ToyActionSpriteProps): JSX.Element | null {
  const size = props.size ?? 112;

  // Phase V: animatingSlot drives the data-animating attribute.
  // Non-null = intro animation is playing; null = animation complete.
  // Using state (not ref) so setting it triggers the re-render needed
  // to add/remove the data-animating attribute from the DOM element.
  const [animatingSlot, setAnimatingSlot] = useState<string | null>(props.slot);

  // Ref tracking which slot was animating when the intro started.
  // Guards against the stale-closure race where slot changes mid-animation:
  // if the img's onAnimationEnd fires after a slot prop change, the handler
  // reads the ref (set at effect time) rather than the potentially-stale
  // closure over props.slot.
  const animatingSlotRef = useRef<string | null>(props.slot);

  // Format-chain cursor. ``attempt`` indexes ``formats``; ``hidden`` is
  // set once every candidate 404s (the element unmounts so a missing
  // sprite reads as "no sprite for this step" instead of a broken img).
  const formats = props.preferSvg ? SVG_FIRST : PNG_ONLY;
  const [attempt, setAttempt] = useState(0);
  const [hidden, setHidden] = useState(false);

  // On slot- OR preferSvg-prop change (and mount): restart the intro
  // animation, reset the format cursor to the first candidate, and clear
  // any prior hidden state.
  useEffect(() => {
    animatingSlotRef.current = props.slot;
    setAnimatingSlot(props.slot);
    setAttempt(0);
    setHidden(false);
  }, [props.slot, props.preferSvg]);

  if (hidden) return null;

  // Clamp defensively: a mid-render preferSvg flip can leave ``attempt``
  // past the new list's end for one render before the effect resets it.
  const ext = formats[Math.min(attempt, formats.length - 1)];

  const alt =
    props.toyDisplayName !== undefined && props.toyDisplayName.length > 0
      ? `${props.toyDisplayName} ${props.slot}`
      : props.slot;

  const baseStyle: CSSProperties = {
    width: size,
    height: size,
    flexShrink: 0,
    objectFit: "contain",
    background: "transparent",
  };
  const merged: CSSProperties = { ...baseStyle, ...(props.style ?? {}) };

  const baseUrl = `/api/static/images/toy_actions/${props.toyId}/${props.slot}.${ext}`;
  const src =
    props.cacheKey !== undefined
      ? `${baseUrl}?v=${encodeURIComponent(props.cacheKey)}`
      : baseUrl;

  // onAnimationEnd clears the intro-animation state. There is no longer a
  // raster format swap on idle — the steady state is whatever single
  // format loaded (an idle ``.svg`` self-animates internally). Reading
  // from the ref (not props.slot) avoids a stale-closure race if the slot
  // prop changes mid-animation.
  const handleAnimationEnd = () => {
    animatingSlotRef.current = null;
    setAnimatingSlot(null);
  };

  // A 404 advances to the next candidate format (svg → png); once the
  // chain is exhausted the element hides so the step renders without a
  // broken image (capability disabled, generation not yet finished, or
  // failed).
  const handleError = () => {
    if (attempt < formats.length - 1) {
      setAttempt(attempt + 1);
    } else {
      setHidden(true);
    }
  };

  return (
    <img
      data-testid="toy-action-sprite"
      data-slot={props.slot}
      data-toy-id={props.toyId}
      data-animating={animatingSlot ?? undefined}
      src={src}
      alt={alt}
      width={size}
      height={size}
      onAnimationEnd={handleAnimationEnd}
      onError={handleError}
      style={merged}
    />
  );
}
