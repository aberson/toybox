// Phase K Step K8 — tts.ts substrate tests.
//
// Vitest defaults this kiosk module to the node environment (see
// ``vitest.config.ts``) — ``window`` and ``window.speechSynthesis`` do
// not exist unless we stage them. Each test sets a fake
// ``window.speechSynthesis`` + ``window.SpeechSynthesisUtterance`` on
// ``globalThis`` and tears down in ``afterEach`` to keep cases
// independent.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetTtsStateForTests,
  cancel,
  isUnlocked,
  speak,
} from "./tts";
import type { VoiceProfile } from "./tts";

interface FakeVoice {
  name: string;
}

interface FakeUtteranceShape {
  text: string;
  rate: number;
  pitch: number;
  voice: FakeVoice | null;
  onend: (() => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
}

// Factory: build a fresh fake speechSynthesis + utterance pair per test
// so previous-test state never bleeds in. Returns the spy handles the
// individual tests need to assert against.
function installFakeSynthesis(opts: {
  voices?: FakeVoice[];
  addEventListenerExists?: boolean;
  utteranceCtorThrows?: boolean;
  speakThrows?: boolean;
  voicesChangedEarly?: boolean;
  // #207: engine in-flight state. The real SpeechSynthesis exposes these
  // as booleans; left undefined here, they read as falsy ("not in flight")
  // so the conditional cancel() defaults to its safe no-op path.
  speaking?: boolean;
  pending?: boolean;
} = {}) {
  const utterances: FakeUtteranceShape[] = [];
  const speakSpy = vi.fn();
  const cancelSpy = vi.fn();
  const addEventListenerSpy = vi.fn();
  // Voices: a getter so we can mutate the underlying array across the
  // test's lifetime (priming the voiceschanged race).
  let voicesArr: FakeVoice[] = opts.voices ?? [];

  class FakeUtterance implements FakeUtteranceShape {
    text: string;
    rate: number = 1;
    pitch: number = 1;
    voice: FakeVoice | null = null;
    onend: (() => void) | null = null;
    onerror: ((ev: { error: string }) => void) | null = null;
    constructor(text: string) {
      if (opts.utteranceCtorThrows === true) {
        throw new Error("utterance ctor failed");
      }
      this.text = text;
      utterances.push(this);
    }
  }

  const synth = {
    getVoices: vi.fn(() => voicesArr),
    speak: vi.fn((u: FakeUtteranceShape) => {
      speakSpy(u);
      if (opts.speakThrows === true) {
        throw new Error("speak failed");
      }
    }),
    cancel: cancelSpy,
    speaking: opts.speaking,
    pending: opts.pending,
    ...(opts.addEventListenerExists === false
      ? {}
      : { addEventListener: addEventListenerSpy }),
  };

  (globalThis as { window?: unknown }).window = {
    speechSynthesis: synth,
    SpeechSynthesisUtterance: FakeUtterance,
  };

  return {
    synth,
    speakSpy,
    cancelSpy,
    addEventListenerSpy,
    utterances,
    setVoices(v: FakeVoice[]): void {
      voicesArr = v;
    },
  };
}

describe("tts", () => {
  beforeEach(() => {
    _resetTtsStateForTests();
  });

  afterEach(() => {
    _resetTtsStateForTests();
    delete (globalThis as { window?: unknown }).window;
  });

  describe("when speechSynthesis API is unavailable", () => {
    it("speak() resolves immediately and silently", async () => {
      // No window staged — purely node env.
      const profile: VoiceProfile = { rate: 1.0, pitch: 1.0 };
      await expect(speak("hello", profile)).resolves.toBeUndefined();
    });

    it("cancel() is a silent no-op", () => {
      expect(() => cancel()).not.toThrow();
    });

    it("isUnlocked() returns false", () => {
      expect(isUnlocked()).toBe(false);
    });
  });

  describe("speak()", () => {
    it("constructs an utterance with the profile's rate and pitch", async () => {
      const fake = installFakeSynthesis();
      const profile: VoiceProfile = { rate: 1.5, pitch: 0.8 };
      const p = speak("hello world", profile);
      // Settle the synchronous bookkeeping then fire onend.
      expect(fake.utterances).toHaveLength(1);
      const u = fake.utterances[0]!;
      expect(u.text).toBe("hello world");
      expect(u.rate).toBe(1.5);
      expect(u.pitch).toBe(0.8);
      // Trigger the resolve path.
      u.onend?.();
      await expect(p).resolves.toBeUndefined();
    });

    it("calls speechSynthesis.speak() exactly once per call", async () => {
      const fake = installFakeSynthesis();
      const p = speak("a", { rate: 1, pitch: 1 });
      expect(fake.synth.speak).toHaveBeenCalledTimes(1);
      fake.utterances[0]!.onend?.();
      await p;
    });

    it("rejects with a typed Error on utterance onerror", async () => {
      const fake = installFakeSynthesis();
      const p = speak("oops", { rate: 1, pitch: 1 });
      fake.utterances[0]!.onerror?.({ error: "interrupted" });
      await expect(p).rejects.toThrow("speechSynthesis: interrupted");
    });

    it("rejects when the SpeechSynthesisUtterance constructor throws", async () => {
      installFakeSynthesis({ utteranceCtorThrows: true });
      await expect(speak("x", { rate: 1, pitch: 1 })).rejects.toThrow(
        "utterance ctor failed",
      );
    });

    it("rejects when speechSynthesis.speak() throws synchronously", async () => {
      installFakeSynthesis({ speakThrows: true });
      await expect(speak("x", { rate: 1, pitch: 1 })).rejects.toThrow(
        "speak failed",
      );
    });

    describe("voice-name resolution", () => {
      it("uses the named voice when found in the bootstrapped list", async () => {
        const target: FakeVoice = { name: "Wizardly Voice" };
        const fake = installFakeSynthesis({
          voices: [{ name: "Other" }, target, { name: "Third" }],
        });
        const p = speak("magic", {
          rate: 1,
          pitch: 1,
          voiceName: "Wizardly Voice",
        });
        expect(fake.utterances[0]!.voice).toBe(target);
        fake.utterances[0]!.onend?.();
        await p;
      });

      it("falls back to engine default when voiceName is set but not found", async () => {
        const fake = installFakeSynthesis({
          voices: [{ name: "Other" }],
        });
        const p = speak("magic", {
          rate: 1,
          pitch: 1,
          voiceName: "Missing Voice",
        });
        // utterance.voice stays null — engine picks default.
        expect(fake.utterances[0]!.voice).toBeNull();
        // No throw on the missing voice — graceful fallback per spec.
        fake.utterances[0]!.onend?.();
        await expect(p).resolves.toBeUndefined();
      });

      it("leaves utterance.voice unset when no voiceName given", async () => {
        const fake = installFakeSynthesis({
          voices: [{ name: "Default" }],
        });
        const p = speak("plain", { rate: 1, pitch: 1 });
        expect(fake.utterances[0]!.voice).toBeNull();
        fake.utterances[0]!.onend?.();
        await p;
      });
    });

    describe("voiceschanged listener", () => {
      it("attaches addEventListener('voiceschanged', ...) on first speak", async () => {
        const fake = installFakeSynthesis();
        const p = speak("x", { rate: 1, pitch: 1 });
        expect(fake.addEventListenerSpy).toHaveBeenCalledTimes(1);
        expect(fake.addEventListenerSpy.mock.calls[0]![0]).toBe(
          "voiceschanged",
        );
        expect(typeof fake.addEventListenerSpy.mock.calls[0]![1]).toBe(
          "function",
        );
        fake.utterances[0]!.onend?.();
        await p;
      });

      it("only attaches the listener once across multiple speak calls", async () => {
        const fake = installFakeSynthesis();
        const p1 = speak("a", { rate: 1, pitch: 1 });
        fake.utterances[0]!.onend?.();
        await p1;
        const p2 = speak("b", { rate: 1, pitch: 1 });
        fake.utterances[1]!.onend?.();
        await p2;
        expect(fake.addEventListenerSpy).toHaveBeenCalledTimes(1);
      });

      it("falls back to onvoiceschanged property when addEventListener is missing", async () => {
        // Stage a synth without addEventListener (older Safari).
        const synthShape: {
          getVoices: () => FakeVoice[];
          speak: (u: FakeUtteranceShape) => void;
          cancel: () => void;
          onvoiceschanged?: () => void;
        } = {
          getVoices: () => [],
          speak: vi.fn(),
          cancel: vi.fn(),
        };
        class FakeUtterance implements FakeUtteranceShape {
          text: string;
          rate = 1;
          pitch = 1;
          voice: FakeVoice | null = null;
          onend: (() => void) | null = null;
          onerror: ((ev: { error: string }) => void) | null = null;
          constructor(text: string) {
            this.text = text;
          }
        }
        (globalThis as { window?: unknown }).window = {
          speechSynthesis: synthShape,
          SpeechSynthesisUtterance: FakeUtterance,
        };
        // First speak() attaches via property — verify the handler ran.
        void speak("x", { rate: 1, pitch: 1 }).catch(() => {
          // No onend wired up in this lightweight fixture; that's fine,
          // we only care about listener attachment.
        });
        expect(typeof synthShape.onvoiceschanged).toBe("function");
      });

      it("repopulates the voice cache when voiceschanged fires", async () => {
        // Stage with empty voices, then mutate the array AFTER the
        // listener attaches, then trigger the listener via the
        // captured callback.
        const fake = installFakeSynthesis({ voices: [] });
        const newVoice: FakeVoice = { name: "Late Arrival" };
        // First speak: cache will be empty, but a voiceName lookup
        // attempts a synchronous refresh too. To test the LISTENER
        // path specifically, simulate the engine's late voice list:
        fake.setVoices([newVoice]);
        // Capture the handler the listener attached.
        const handler = fake.addEventListenerSpy.mock.calls[0]?.[1] as
          | (() => void)
          | undefined;
        // Force the first listener-attach by speaking once.
        const primer = speak("primer", { rate: 1, pitch: 1 });
        // Handler may have been captured during primer; re-grab if so.
        const handlerAfter =
          handler ??
          (fake.addEventListenerSpy.mock.calls[0]?.[1] as
            | (() => void)
            | undefined);
        expect(handlerAfter).toBeDefined();
        // Fire the voiceschanged callback — cache should now have
        // newVoice so the next speak() can resolve it by name.
        handlerAfter!();
        fake.utterances[0]!.onend?.();
        await primer;
        // Second speak with voiceName = "Late Arrival": now resolves.
        const p2 = speak("after", {
          rate: 1,
          pitch: 1,
          voiceName: "Late Arrival",
        });
        expect(fake.utterances[1]!.voice).toEqual(newVoice);
        fake.utterances[1]!.onend?.();
        await p2;
      });
    });

    describe("gesture-unlock state machine", () => {
      it("starts unlocked = false", () => {
        installFakeSynthesis();
        expect(isUnlocked()).toBe(false);
      });

      it("first successful speak() flips unlocked = true", async () => {
        const fake = installFakeSynthesis();
        expect(isUnlocked()).toBe(false);
        const p = speak("hi", { rate: 1, pitch: 1 });
        // The flag is set synchronously after speak() dispatches.
        expect(isUnlocked()).toBe(true);
        fake.utterances[0]!.onend?.();
        await p;
      });

      it("unlocked persists across multiple speak calls within a session", async () => {
        const fake = installFakeSynthesis();
        const p1 = speak("one", { rate: 1, pitch: 1 });
        fake.utterances[0]!.onend?.();
        await p1;
        expect(isUnlocked()).toBe(true);
        const p2 = speak("two", { rate: 1, pitch: 1 });
        // Still true between calls.
        expect(isUnlocked()).toBe(true);
        fake.utterances[1]!.onend?.();
        await p2;
        expect(isUnlocked()).toBe(true);
      });

      it("unlocked stays false when speak() throws before dispatch", async () => {
        installFakeSynthesis({ speakThrows: true });
        await expect(speak("x", { rate: 1, pitch: 1 })).rejects.toThrow();
        expect(isUnlocked()).toBe(false);
      });

      it("unlocked stays false when the utterance constructor throws", async () => {
        installFakeSynthesis({ utteranceCtorThrows: true });
        await expect(speak("x", { rate: 1, pitch: 1 })).rejects.toThrow();
        expect(isUnlocked()).toBe(false);
      });

      it("_resetTtsStateForTests() clears unlocked back to false", async () => {
        const fake = installFakeSynthesis();
        const p = speak("x", { rate: 1, pitch: 1 });
        fake.utterances[0]!.onend?.();
        await p;
        expect(isUnlocked()).toBe(true);
        _resetTtsStateForTests();
        expect(isUnlocked()).toBe(false);
      });
    });
  });

  describe("cancel()", () => {
    it("calls speechSynthesis.cancel() when an utterance is speaking", () => {
      const fake = installFakeSynthesis({ speaking: true });
      cancel();
      expect(fake.cancelSpy).toHaveBeenCalledTimes(1);
    });

    it("calls speechSynthesis.cancel() when an utterance is pending (speaking=false)", () => {
      const fake = installFakeSynthesis({ speaking: false, pending: true });
      cancel();
      expect(fake.cancelSpy).toHaveBeenCalledTimes(1);
    });

    // #207 regression: the iOS cancel-then-speak race only triggers when
    // cancel() actually invokes the native cancel before a speak(). When
    // nothing is in flight, cancel() must NOT call the native cancel — that
    // is what lets the common idle path speak cleanly on iOS Safari.
    it("does NOT call speechSynthesis.cancel() when the engine is idle (speaking=false, pending=false)", () => {
      const fake = installFakeSynthesis({ speaking: false, pending: false });
      cancel();
      expect(fake.cancelSpy).not.toHaveBeenCalled();
    });

    it("does NOT call speechSynthesis.cancel() when speaking/pending are unreported (undefined)", () => {
      const fake = installFakeSynthesis();
      cancel();
      expect(fake.cancelSpy).not.toHaveBeenCalled();
    });

    // #207: the call-site pattern is cancel(); speak();. On an idle engine
    // the native cancel must be skipped entirely so the new utterance
    // dispatches without tripping the WebKit canceled-utterance bug.
    it("idle interrupt-then-speak (the #207 pattern) never invokes native cancel before speak", async () => {
      const fake = installFakeSynthesis({ speaking: false, pending: false });
      cancel();
      const p = speak("hello", { rate: 1, pitch: 1 });
      expect(fake.cancelSpy).not.toHaveBeenCalled();
      expect(fake.speakSpy).toHaveBeenCalledTimes(1);
      fake.utterances[0]!.onend?.();
      await p;
    });

    it("is safe to call when the in-flight engine's cancel() throws", () => {
      // Stage a synth that reports speaking (so the guard lets cancel
      // through) but whose cancel() throws — we should swallow it.
      (globalThis as { window?: unknown }).window = {
        speechSynthesis: {
          getVoices: () => [],
          speak: vi.fn(),
          speaking: true,
          pending: false,
          cancel: () => {
            throw new Error("nothing to cancel");
          },
          addEventListener: vi.fn(),
        },
        SpeechSynthesisUtterance: class {
          constructor(public text: string) {}
        },
      };
      expect(() => cancel()).not.toThrow();
    });

    it("rejects an in-flight speak() Promise when cancel() fires its onerror", async () => {
      // speaking=true so the conditional cancel() actually invokes the
      // native cancel (the path that surfaces onerror on outstanding
      // utterances).
      const fake = installFakeSynthesis({ speaking: true });
      const inflight = speak("hello", { rate: 1, pitch: 1 });
      cancel();
      expect(fake.cancelSpy).toHaveBeenCalledTimes(1);
      // Engines surface cancel() to outstanding utterances via onerror
      // with code "interrupted"; the wrapper must translate that into a
      // typed rejection so K9/K12 callers can distinguish kid-cancel
      // from real failure.
      fake.utterances[0]!.onerror?.({
        error: "interrupted",
      } as SpeechSynthesisErrorEvent);
      await expect(inflight).rejects.toThrow(/interrupted/);
    });
  });
});
