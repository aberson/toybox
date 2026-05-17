// Phase L Step L10: typed CSSProperties map for the kiosk-side
// runtime reward animations.
//
// Mirrors the parent-preview map at
// ``frontend/src/parent/animations/rewardAnimationsPreview.ts`` but
// references this module's un-prefixed keyframe names. Duplication is
// intentional — the preview lifecycle (loops indefinitely while the
// parent stages a reward) differs from the kiosk runtime lifecycle
// (plays during the picture reward reveal) so a single shared map
// would conflate two concerns.
//
// The ``import "./rewardAnimations.css"`` side-effect ensures the
// keyframes are registered in the kiosk's CSS bundle whenever the
// REWARD_ANIMATIONS map is imported — no separate import needed at
// the consumer side.

import type { CSSProperties } from "react";

import type { Animation } from "../../shared/types";

import "./rewardAnimations.css";

export const REWARD_ANIMATIONS: Record<Animation, CSSProperties> = {
  shine: {
    animation: "shine 2s ease-in-out infinite",
  },
  jump: {
    animation: "jump 1.5s cubic-bezier(0.34, 1.56, 0.64, 1) infinite",
  },
  spin: {
    animation: "spin 2s linear infinite",
  },
  pulse: {
    animation: "pulse 1.2s ease-in-out infinite",
  },
  wobble: {
    animation: "wobble 1s ease-in-out infinite",
  },
  float: {
    animation: "float 3s ease-in-out infinite",
  },
};
