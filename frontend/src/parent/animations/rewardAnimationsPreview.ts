// Phase L Step L7: typed CSSProperties map for the six reward
// preview animations.
//
// The CSS file (``rewardAnimationsPreview.css``) ships the keyframes;
// this module maps :class:`Animation` enum values to the inline
// ``animation`` shorthand string the RewardIngest preview card applies
// via the ``style`` prop.
//
// The kiosk-side renderer (L10) will ship its own
// ``frontend/src/child/animations/rewardAnimations.ts`` typed map
// against ``rewardAnimations.css``. Duplication is intentional — the
// preview lifecycle (loops indefinitely while the parent is staging a
// reward) differs from the runtime lifecycle (plays once when the
// kiosk reveals the reward) so a single shared map would conflate two
// concerns.

import type { CSSProperties } from "react";

import type { Animation } from "../../shared/types";

// One ``animation`` shorthand value per enum member. The keyframe
// names are namespaced ``reward-preview-*`` so a future kiosk-side
// import of its own CSS file can't collide on global keyframe scope.
//
// Order matches the Python enum definition order (shine, jump, spin,
// pulse, wobble, float) — used by the segmented control so the buttons
// render in the spec-defined sequence.
export const REWARD_PREVIEW_ANIMATIONS: Record<Animation, CSSProperties> = {
  shine: {
    animation: "reward-preview-shine 2s ease-in-out infinite",
  },
  jump: {
    animation:
      "reward-preview-jump 1.5s cubic-bezier(0.34, 1.56, 0.64, 1) infinite",
  },
  spin: {
    animation: "reward-preview-spin 2s linear infinite",
  },
  pulse: {
    animation: "reward-preview-pulse 1.2s ease-in-out infinite",
  },
  wobble: {
    animation: "reward-preview-wobble 1s ease-in-out infinite",
  },
  float: {
    animation: "reward-preview-float 3s ease-in-out infinite",
  },
};

// Display labels for the segmented control buttons. Order MUST match
// the Python ``Animation`` enum definition (shine → float) so the UI
// renders in spec order. Tests assert this list verbatim.
export const ANIMATION_OPTIONS: readonly Animation[] = [
  "shine",
  "jump",
  "spin",
  "pulse",
  "wobble",
  "float",
] as const;

export const ANIMATION_LABELS: Record<Animation, string> = {
  shine: "Shine",
  jump: "Jump",
  spin: "Spin",
  pulse: "Pulse",
  wobble: "Wobble",
  float: "Float",
};
