// Phase S Step S1 — gradientForPersona unit tests.
//
// Coverage:
//   - Each of the 4 named persona_ids returns a distinct string.
//   - null, undefined, empty string, and unknown inputs return the fallback.
//   - Every return value contains "linear-gradient".

import { describe, expect, it } from "vitest";

import { gradientForPersona, PERSONA_GRADIENT_FALLBACK } from "./theming";

const KNOWN_PERSONAS = [
  "detective",
  "periodic_table",
  "princess",
  "wizard",
] as const;

describe("gradientForPersona — known personas", () => {
  it("returns distinct strings for all 4 known personas + null fallback", () => {
    const values = new Set<string>();
    for (const id of KNOWN_PERSONAS) {
      values.add(gradientForPersona(id));
    }
    values.add(gradientForPersona(null));
    // Five distinct values: one per persona + the fallback.
    expect(values.size).toBe(5);
  });

  it.each(KNOWN_PERSONAS)(
    "returns a non-fallback gradient for persona_id=%s",
    (id) => {
      const result = gradientForPersona(id);
      expect(result).not.toBe(PERSONA_GRADIENT_FALLBACK);
    },
  );

  it.each(KNOWN_PERSONAS)(
    "gradient for %s contains 'linear-gradient'",
    (id) => {
      expect(gradientForPersona(id)).toContain("linear-gradient");
    },
  );
});

describe("gradientForPersona — fallback cases", () => {
  it("returns the fallback for null", () => {
    expect(gradientForPersona(null)).toBe(PERSONA_GRADIENT_FALLBACK);
  });

  it("returns the fallback for undefined", () => {
    expect(gradientForPersona(undefined)).toBe(PERSONA_GRADIENT_FALLBACK);
  });

  it("returns the fallback for an empty string", () => {
    expect(gradientForPersona("")).toBe(PERSONA_GRADIENT_FALLBACK);
  });

  it("returns the fallback for an unknown persona_id", () => {
    expect(gradientForPersona("dragon_queen")).toBe(PERSONA_GRADIENT_FALLBACK);
    expect(gradientForPersona("unknown_persona")).toBe(PERSONA_GRADIENT_FALLBACK);
  });

});
