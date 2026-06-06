// Phase S Step S1 — persona-keyed kiosk background gradients.
//
// Each known persona gets a visually distinct gradient that reinforces
// character identity at the kiosk level. The null / unknown fallback is
// the original near-white idle state so the transition to the idle screen
// is smooth.
//
// No external deps — pure string mapping so this module is cheap to
// import anywhere in the kiosk shell.

const PERSONA_GRADIENTS: Readonly<Record<string, string>> = {
  detective: "linear-gradient(160deg, #1a237e 0%, #37474f 100%)",
  periodic_table: "linear-gradient(160deg, #004d40 0%, #1b5e20 100%)",
  princess: "linear-gradient(160deg, #fce4ec 0%, #e8d5f5 100%)",
  wizard: "linear-gradient(160deg, #311b92 0%, #0d1b4b 100%)",
};

/** Fallback used for null, unknown, or undefined persona_ids. */
export const PERSONA_GRADIENT_FALLBACK =
  "linear-gradient(160deg, #fff8e1 0%, #ffe0b2 100%)";

/**
 * Returns a CSS `background` gradient string for the given persona_id.
 *
 * - Known persona_ids map to their character-specific gradient.
 * - `null`, `undefined`, an empty string, or any unknown id returns the
 *   warm-idle fallback (matching the original static near-white gradient).
 */
export function gradientForPersona(persona_id: string | null | undefined): string {
  if (persona_id === null || persona_id === undefined || persona_id === "") {
    return PERSONA_GRADIENT_FALLBACK;
  }
  return PERSONA_GRADIENTS[persona_id] ?? PERSONA_GRADIENT_FALLBACK;
}
