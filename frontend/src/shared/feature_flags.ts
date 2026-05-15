// Phase K step K2: SHARED canonical declaration of the eight
// parent-controlled feature flags. Both the parent UI (api.ts +
// SettingsPanel + PlayFeaturesControls) and the kiosk (child/api.ts +
// App.tsx bootstrap) import from this module — one source of truth
// per code-quality §2.
//
// The keys are the canonical snake_case Pydantic names — they match
// the rows in the ``settings`` table seeded by migration 0015 and the
// per-setting Pydantic models in ``src/toybox/core/<flag>.py``. The
// shared/ directory is the right home for this because:
//
//   - shared/types.ts already houses cross-(parent|kiosk) shapes.
//   - The defaults dict is consumed by both the parent's optimistic
//     seed (App.tsx bootstrap) and the kiosk's optimistic seed.
//   - A future ninth flag is a SINGLE-LINE edit here + one entry in
//     ``KIOSK_FEATURE_FLAG_PATHS`` (still local to child/api.ts
//     since it's a kiosk-only fetch routing concern) + backend
//     migration + Pydantic model. The source-of-truth-lock test in
//     ``tests/integration/test_phase_k_feature_flag_lists_agree.py``
//     fails CI if the three lists drift.
//
// Defaults mirror documentation/phase-k-plan.md §5 and the seeded
// migration 0015 row values exactly. All true except
// ``play_spontaneity_enabled`` — the one opt-in flag.

export type PhaseKFeatureFlag =
  | "jokes_enabled"
  | "songs_enabled"
  | "play_standalone_enabled"
  | "play_embedded_enabled"
  | "play_endings_enabled"
  | "play_spontaneity_enabled"
  | "clickable_words_enabled"
  | "read_me_button_enabled";

export type PhaseKFeatureFlags = Record<PhaseKFeatureFlag, boolean>;

export interface FeatureFlagResponse {
  value: boolean;
}

export const PHASE_K_FEATURE_FLAG_DEFAULTS: PhaseKFeatureFlags = {
  jokes_enabled: true,
  songs_enabled: true,
  play_standalone_enabled: true,
  play_embedded_enabled: true,
  play_endings_enabled: true,
  play_spontaneity_enabled: false,
  clickable_words_enabled: true,
  read_me_button_enabled: true,
};
