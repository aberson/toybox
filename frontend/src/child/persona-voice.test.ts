// Phase K Step K8 — persona-voice.ts resolver tests.

import { describe, expect, it } from "vitest";

import {
  DEFAULT_VOICE_PROFILE,
  getVoiceProfile,
} from "./persona-voice";
import type { PersonaMetadata } from "./persona-voice";

describe("persona-voice", () => {
  describe("DEFAULT_VOICE_PROFILE", () => {
    it("matches browser SpeechSynthesisUtterance defaults", () => {
      expect(DEFAULT_VOICE_PROFILE.rate).toBe(1.0);
      expect(DEFAULT_VOICE_PROFILE.pitch).toBe(1.0);
      expect(DEFAULT_VOICE_PROFILE.voiceName).toBeUndefined();
    });

    it("is frozen so callers cannot mutate the shared default", () => {
      // Object.freeze means assignment is a silent no-op in non-strict
      // and a TypeError in strict. Either way the value must not change.
      const original = DEFAULT_VOICE_PROFILE.rate;
      try {
        (DEFAULT_VOICE_PROFILE as { rate: number }).rate = 99;
      } catch {
        // strict mode TypeError — fine, the assignment is rejected.
      }
      expect(DEFAULT_VOICE_PROFILE.rate).toBe(original);
    });
  });

  describe("getVoiceProfile()", () => {
    it("returns the default profile when persona is null", () => {
      expect(getVoiceProfile(null)).toEqual({ rate: 1.0, pitch: 1.0 });
    });

    it("returns the default profile when persona has no voice_profile field", () => {
      const persona: PersonaMetadata = {
        id: "princess",
        display_name: "Princess Lyra",
      };
      expect(getVoiceProfile(persona)).toEqual({ rate: 1.0, pitch: 1.0 });
    });

    it("returns the default profile when voice_profile is explicitly null", () => {
      const persona: PersonaMetadata = {
        id: "custom_dragon",
        display_name: "Custom Dragon",
        voice_profile: null,
      };
      expect(getVoiceProfile(persona)).toEqual({ rate: 1.0, pitch: 1.0 });
    });

    it("reads rate and pitch from a configured voice_profile", () => {
      const persona: PersonaMetadata = {
        id: "princess",
        display_name: "Princess Lyra",
        voice_profile: { rate: 1.0, pitch: 1.4 },
      };
      expect(getVoiceProfile(persona)).toEqual({ rate: 1.0, pitch: 1.4 });
    });

    it("normalizes snake_case voice_name to camelCase voiceName", () => {
      const persona: PersonaMetadata = {
        id: "wizard",
        display_name: "Wizard",
        voice_profile: {
          rate: 0.9,
          pitch: 0.6,
          voice_name: "Wizardly Voice",
        },
      };
      expect(getVoiceProfile(persona)).toEqual({
        rate: 0.9,
        pitch: 0.6,
        voiceName: "Wizardly Voice",
      });
    });

    it("omits voiceName when voice_name is undefined", () => {
      const persona: PersonaMetadata = {
        id: "princess",
        display_name: "Princess Lyra",
        voice_profile: { rate: 1.0, pitch: 1.4 },
      };
      const profile = getVoiceProfile(persona);
      expect(profile.voiceName).toBeUndefined();
      expect(Object.prototype.hasOwnProperty.call(profile, "voiceName")).toBe(
        false,
      );
    });

    it("omits voiceName when voice_name is an empty string", () => {
      const persona: PersonaMetadata = {
        id: "princess",
        display_name: "Princess Lyra",
        voice_profile: { rate: 1.0, pitch: 1.4, voice_name: "" },
      };
      const profile = getVoiceProfile(persona);
      expect(profile.voiceName).toBeUndefined();
    });

    it("falls back to default when rate is missing (malformed envelope)", () => {
      // Forge a wire envelope that passed pydantic but somehow lost a
      // field on the way to the kiosk (e.g. a future serializer bug).
      // Defensive: kiosk must not crash on a malformed profile.
      const persona = {
        id: "broken",
        display_name: "Broken",
        voice_profile: { pitch: 1.0 } as unknown as {
          rate: number;
          pitch: number;
        },
      };
      expect(getVoiceProfile(persona)).toEqual({ rate: 1.0, pitch: 1.0 });
    });

    it("falls back to default when pitch is missing", () => {
      const persona = {
        id: "broken",
        display_name: "Broken",
        voice_profile: { rate: 1.0 } as unknown as {
          rate: number;
          pitch: number;
        },
      };
      expect(getVoiceProfile(persona)).toEqual({ rate: 1.0, pitch: 1.0 });
    });
  });
});
