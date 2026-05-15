// Phase K Step K8 — Persona → VoiceProfile resolver.
//
// The kiosk does NOT currently maintain a separate persona-library
// fetcher. The only persona data the kiosk sees comes embedded in
// ``activity.metadata.persona`` (see ``frontend/src/child/App.tsx``'s
// ``avatarLetter`` / ``avatarImage`` helpers — they read the same
// envelope). This module mirrors that pattern: callers pass the
// already-resolved persona metadata blob (or ``null``) and we return
// the effective VoiceProfile.
//
// Wire shape expectations:
//   ``activity.metadata.persona`` is an object containing AT LEAST
//   ``{ id: string, display_name: string }`` today; Phase K K1 has
//   already shipped the persona library's ``voice_profile`` field
//   (``{rate, pitch, voice_name?}`` or null) but the backend's
//   ``_pick_random_library_persona`` does not yet splice it into the
//   wire envelope. Until then (K9 or K12 will extend it when a real
//   consumer needs it), this resolver returns the default profile —
//   matching the persona-library schema's "NULL = system default"
//   contract.
//
// Default profile = ``{rate: 1.0, pitch: 1.0}`` — matches the
// browser's default ``SpeechSynthesisUtterance`` initial values, so
// "no profile" sounds the same as "no kiosk customization".

import type { VoiceProfile } from "./tts";

/**
 * Wire-side persona metadata as it appears on
 * ``activity.metadata.persona`` (and as ``_pick_random_library_persona``
 * builds it in ``src/toybox/api/activities.py``). Every field is
 * optional on this client-side type because the kiosk has to tolerate
 * pre-K, partial-K, and post-K backend variants — invariant 9's
 * pydantic→TS codegen does NOT cover ``metadata`` (it's typed
 * ``dict[str, Any]`` on the wire), so we read it defensively.
 */
export interface PersonaMetadata {
  id?: string;
  display_name?: string;
  archetype?: string;
  avatar_image_path?: string | null;
  // Phase K K1 persona-library voice profile. ``null`` (or absent) =
  // use the system default. When present, ``rate`` and ``pitch`` are
  // required (validated server-side by ``VoiceProfile`` pydantic);
  // ``voice_name`` is optional and may not match a voice that exists
  // on the kid's device — see ``tts.ts``'s fallback behavior.
  voice_profile?: {
    rate: number;
    pitch: number;
    voice_name?: string;
  } | null;
}

/**
 * Default VoiceProfile applied when no persona is active OR the
 * persona explicitly opts out of customization (``voice_profile: null``).
 * Constants chosen to match the browser's ``SpeechSynthesisUtterance``
 * default rate/pitch so callers can swap in / swap out the profile
 * without an audible jump.
 */
export const DEFAULT_VOICE_PROFILE: VoiceProfile = Object.freeze({
  rate: 1.0,
  pitch: 1.0,
});

/**
 * Resolve a persona metadata object to its VoiceProfile.
 *
 * - ``null`` / undefined persona → default profile.
 * - persona with no ``voice_profile`` (pre-K1 wire payload OR custom
 *   persona that opted out) → default profile.
 * - persona with ``voice_profile: null`` (explicit null per the
 *   pydantic VoiceProfile contract) → default profile.
 * - persona with a non-null ``voice_profile`` → that profile,
 *   normalized to the TS-side ``VoiceProfile`` shape (snake_case
 *   ``voice_name`` → camelCase ``voiceName``).
 *
 * Never throws; out-of-range rate/pitch values are passed through
 * unmodified (``tts.ts`` lets the engine clamp). Out-of-band values
 * indicate a backend validator regression, not a runtime kiosk
 * concern — surface in tests, not in user UX.
 */
export function getVoiceProfile(persona: PersonaMetadata | null): VoiceProfile {
  if (persona === null || persona === undefined) {
    return DEFAULT_VOICE_PROFILE;
  }
  const vp = persona.voice_profile;
  if (vp === null || vp === undefined) {
    return DEFAULT_VOICE_PROFILE;
  }
  // Defensive: a malformed envelope (e.g. ``voice_profile: {}``) would
  // pass static typing if a caller stringly-typed past PersonaMetadata.
  // Treat any missing required scalar as a default fall-through so the
  // kiosk never speaks at NaN-pitch.
  if (typeof vp.rate !== "number" || typeof vp.pitch !== "number") {
    return DEFAULT_VOICE_PROFILE;
  }
  const profile: VoiceProfile = {
    rate: vp.rate,
    pitch: vp.pitch,
  };
  if (typeof vp.voice_name === "string" && vp.voice_name.length > 0) {
    profile.voiceName = vp.voice_name;
  }
  return profile;
}
