// Phase K step K2: SHARED canonical declaration of the parent-controlled
// feature flags. Originally eight; Phase L Step L5 removed the three
// Phase K play-surface flags (``play_embedded_enabled``,
// ``play_endings_enabled``, ``play_spontaneity_enabled``) when
// jokes/songs migrated to per-activity reward types. Both the parent UI
// (api.ts + SettingsPanel + PlayFeaturesControls) and the kiosk
// (child/api.ts + App.tsx bootstrap) import from this module — one
// source of truth per code-quality §2.
//
// The keys are the canonical snake_case Pydantic names — they match
// the rows in the ``settings`` table seeded by migration 0015 and the
// per-setting Pydantic models in ``src/toybox/core/<flag>.py``. The
// shared/ directory is the right home for this because:
//
//   - shared/types.ts already houses cross-(parent|kiosk) shapes.
//   - The defaults dict is consumed by both the parent's optimistic
//     seed (App.tsx bootstrap) and the kiosk's optimistic seed.
//   - A future flag is a SINGLE-LINE edit here + one entry in
//     ``KIOSK_FEATURE_FLAG_PATHS`` (still local to child/api.ts
//     since it's a kiosk-only fetch routing concern) + backend
//     migration + Pydantic model. The source-of-truth-lock test in
//     ``tests/integration/test_phase_k_feature_flag_lists_agree.py``
//     fails CI if the three lists drift.
//
// Defaults mirror documentation/phase-k-plan.md §5 and the seeded
// migration 0015 row values exactly. All true after Phase L Step L5.
// Phase Z Z6 added ``neural_voice_enabled`` (migration 0031, default
// true) — the kiosk-wide gate for Z5 neural-voice clip playback.

export type PhaseKFeatureFlag =
  | "jokes_enabled"
  | "songs_enabled"
  | "play_standalone_enabled"
  | "clickable_words_enabled"
  | "read_me_button_enabled"
  | "neural_voice_enabled";

export type PhaseKFeatureFlags = Record<PhaseKFeatureFlag, boolean>;

export interface FeatureFlagResponse {
  value: boolean;
}

export const PHASE_K_FEATURE_FLAG_DEFAULTS: PhaseKFeatureFlags = {
  jokes_enabled: true,
  songs_enabled: true,
  play_standalone_enabled: true,
  clickable_words_enabled: true,
  read_me_button_enabled: true,
  neural_voice_enabled: true,
};
