// Phase Z Z5 — clip-audio substrate tests.
//
// Runs under happy-dom (see vitest.config.ts environmentMatchGlobs) so
// a real ``window`` exists; the tests stage their own ``window.Audio``
// fake so play/ended/error/pause behavior is fully controllable.
//
// Coverage:
//   - playClip resolves on ``ended``, rejects on ``error`` (404/decode),
//     rejects on play() rejection (autoplay policy).
//   - ONE shared element across calls (the iOS per-element-unlock
//     rationale) — a second playClip interrupts the first.
//   - Interruption rejections are distinguishable (isClipInterrupted)
//     from fallback-worthy failures.
//   - stopClip pauses + rejects-as-interrupted; no-op when idle.
//   - playClip cancels Web Speech (single audio focus, clip side).
//   - primeClipAudio plays a silent data-URI and pauses after unlock;
//     never clobbers an in-flight clip.
//   - Non-DOM degrade: missing window.Audio → playClip rejects (the
//     caller's fallback chain lands on Web Speech).
//   - Z4 wire-shape accessors + the effectiveClipUrl gate.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetClipAudioForTests,
  effectiveClipUrl,
  isClipInterrupted,
  playClip,
  primeClipAudio,
  readSpokenAudioUrl,
  readSpokenChoiceAudioUrls,
  readSpokenPunchlineAudioUrl,
  readSpokenSetupAudioUrl,
  stopClip,
} from "./clip-audio";

// clip-audio's single-audio-focus contract calls tts.cancel() before a
// clip starts. Mock the substrate so the assertion is on the SEAM (the
// call), not on a fake speechSynthesis.
vi.mock("./tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

import * as tts from "./tts";

// Controllable HTMLAudioElement stand-in. Only the surface clip-audio
// touches is implemented: src/preload/play/pause + ended/error events.
class FakeAudio {
  static instances: FakeAudio[] = [];
  // Next play() outcome, consumed per-call. null → resolve.
  static nextPlayRejection: Error | null = null;

  src = "";
  preload = "";
  paused = true;
  private listeners: Record<string, Array<() => void>> = {};

  constructor() {
    FakeAudio.instances.push(this);
  }

  addEventListener(name: string, fn: () => void): void {
    (this.listeners[name] ??= []).push(fn);
  }

  removeEventListener(name: string, fn: () => void): void {
    this.listeners[name] = (this.listeners[name] ?? []).filter(
      (f) => f !== fn,
    );
  }

  dispatch(name: string): void {
    // Copy — a handler may remove itself mid-iteration.
    for (const fn of [...(this.listeners[name] ?? [])]) fn();
  }

  listenerCount(name: string): number {
    return (this.listeners[name] ?? []).length;
  }

  play(): Promise<void> {
    this.paused = false;
    const rejection = FakeAudio.nextPlayRejection;
    FakeAudio.nextPlayRejection = null;
    return rejection === null ? Promise.resolve() : Promise.reject(rejection);
  }

  pause(): void {
    this.paused = true;
  }
}

const CLIP_URL = "/api/static/tts/af_heart/abc123def456ab00.wav";
const CLIP_URL_2 = "/api/static/tts/am_michael/00ffee00ffee00ff.wav";

// Flush pending microtasks (promise continuations).
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  _resetClipAudioForTests();
  FakeAudio.instances = [];
  FakeAudio.nextPlayRejection = null;
  (window as unknown as { Audio: unknown }).Audio = FakeAudio;
  vi.clearAllMocks();
});

afterEach(() => {
  _resetClipAudioForTests();
});

describe("playClip — lifecycle", () => {
  it("resolves when the element fires ended", async () => {
    const p = playClip(CLIP_URL);
    const el = FakeAudio.instances[0]!;
    expect(el.src).toBe(CLIP_URL);
    expect(el.paused).toBe(false);
    el.dispatch("ended");
    await expect(p).resolves.toBeUndefined();
  });

  it("rejects on the element's error event (404 / decode) — NOT as an interruption", async () => {
    const p = playClip(CLIP_URL);
    FakeAudio.instances[0]!.dispatch("error");
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    // A load failure must route callers to the Web Speech fallback —
    // it must never be mistaken for an intentional interrupt.
    expect(isClipInterrupted(err)).toBe(false);
  });

  it("rejects when play() rejects (autoplay policy) — NOT as an interruption", async () => {
    FakeAudio.nextPlayRejection = new Error("NotAllowedError: no gesture");
    const p = playClip(CLIP_URL);
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    expect(isClipInterrupted(err)).toBe(false);
  });

  it("removes its event listeners after settling (no leak across clips)", async () => {
    const p = playClip(CLIP_URL);
    const el = FakeAudio.instances[0]!;
    expect(el.listenerCount("ended")).toBe(1);
    expect(el.listenerCount("error")).toBe(1);
    el.dispatch("ended");
    await p;
    expect(el.listenerCount("ended")).toBe(0);
    expect(el.listenerCount("error")).toBe(0);
  });

  it("rejects when window.Audio is unavailable (non-DOM degrade → fallback chain)", async () => {
    delete (window as unknown as { Audio?: unknown }).Audio;
    const err = await playClip(CLIP_URL).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    expect(isClipInterrupted(err)).toBe(false);
  });

  it("settles exactly once when a 404 fires BOTH the error event and the play() rejection", async () => {
    // The realistic 404 shape: the element dispatches ``error`` AND the
    // pending play() promise rejects (NotSupportedError). The settled
    // flag must make whichever lands second a no-op — one rejection,
    // listeners cleaned up, no double-settle.
    FakeAudio.nextPlayRejection = new Error("NotSupportedError: 404");
    const p = playClip(CLIP_URL);
    const el = FakeAudio.instances[0]!;
    // The error event lands first (synchronously here); the play()
    // rejection is still queued on the microtask queue behind it.
    el.dispatch("error");
    await flush(); // let the late play() rejection land on the settled clip
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    expect(isClipInterrupted(err)).toBe(false);
    // The first settle won and cleaned up; the second attempt was a
    // no-op (no listener churn, no re-registration).
    expect(el.listenerCount("ended")).toBe(0);
    expect(el.listenerCount("error")).toBe(0);
  });
});

describe("playClip — single audio focus + shared element", () => {
  it("cancels in-flight Web Speech before starting (tts.cancel, #207 guard preserved inside tts)", () => {
    const p = playClip(CLIP_URL);
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    FakeAudio.instances[0]!.dispatch("ended");
    return p;
  });

  it("reuses ONE shared element across calls (iOS unlock is per-element)", async () => {
    const p1 = playClip(CLIP_URL);
    FakeAudio.instances[0]!.dispatch("ended");
    await p1;
    const p2 = playClip(CLIP_URL_2);
    expect(FakeAudio.instances).toHaveLength(1);
    FakeAudio.instances[0]!.dispatch("ended");
    await p2;
  });

  it("a second playClip interrupts the first: first rejects AS interrupted, second wins", async () => {
    const p1 = playClip(CLIP_URL);
    const p2 = playClip(CLIP_URL_2);
    const err1 = await p1.then(
      () => null,
      (e: unknown) => e,
    );
    expect(isClipInterrupted(err1)).toBe(true);
    const el = FakeAudio.instances[0]!;
    expect(el.src).toBe(CLIP_URL_2);
    el.dispatch("ended");
    await expect(p2).resolves.toBeUndefined();
  });

  it("the interrupted clip's stale play() rejection cannot settle the new clip", async () => {
    // First clip's play() rejects LATE (after the second clip started).
    // The settled-flag must swallow it so p2 still resolves on ended.
    FakeAudio.nextPlayRejection = new Error("late abort");
    const p1 = playClip(CLIP_URL);
    const p2 = playClip(CLIP_URL_2); // interrupts p1 before its play() rejection lands
    await flush(); // let the stale rejection propagate
    const err1 = await p1.then(
      () => null,
      (e: unknown) => e,
    );
    // Interruption won the race — p1 settled as interrupted, and the
    // stale rejection was a no-op.
    expect(isClipInterrupted(err1)).toBe(true);
    FakeAudio.instances[0]!.dispatch("ended");
    await expect(p2).resolves.toBeUndefined();
  });
});

describe("stopClip", () => {
  it("pauses the element and rejects the in-flight playClip as interrupted", async () => {
    const p = playClip(CLIP_URL);
    const el = FakeAudio.instances[0]!;
    expect(el.paused).toBe(false);
    stopClip();
    expect(el.paused).toBe(true);
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(isClipInterrupted(err)).toBe(true);
  });

  it("is a no-op when nothing is playing", () => {
    expect(() => stopClip()).not.toThrow();
    // Never constructs the element just to stop nothing.
    expect(FakeAudio.instances).toHaveLength(0);
  });
});

describe("primeClipAudio", () => {
  it("plays a silent inline WAV and pauses once unlocked (gesture-time prime)", async () => {
    primeClipAudio();
    expect(FakeAudio.instances).toHaveLength(1);
    const el = FakeAudio.instances[0]!;
    expect(el.src.startsWith("data:audio/wav;base64,")).toBe(true);
    expect(el.paused).toBe(false);
    await flush();
    // pause() after play() resolves — the prime leaves the kiosk silent.
    expect(el.paused).toBe(true);
  });

  it("prime WAV carries REAL silent samples, not an empty data chunk (iOS unlock regression pin)", () => {
    // A duration-0 payload (zero-length data chunk) is exactly the edge
    // where iOS unlock payloads historically fail (the Howler/unmute.js
    // lesson — both ship actual silent frames). Pin the wire shape:
    // parse the RIFF header out of the data: URI and assert the data
    // chunk holds non-empty 8-bit-PCM silence.
    primeClipAudio();
    const src = FakeAudio.instances[0]!.src;
    const b64 = src.slice("data:audio/wav;base64,".length);
    const binary = atob(b64);
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    // Canonical 44-byte header: "data" tag at offset 36, size at 40.
    expect(String.fromCharCode(...bytes.slice(36, 40))).toBe("data");
    const view = new DataView(bytes.buffer);
    const dataSize = view.getUint32(40, true);
    expect(dataSize).toBeGreaterThan(0);
    expect(bytes.length).toBe(44 + dataSize);
    // 0x80 is the 8-bit unsigned PCM zero point — actual silence
    // frames, not zero padding (0x00 would be full-negative DC).
    expect(bytes[44]).toBe(0x80);
    expect(bytes[44 + dataSize - 1]).toBe(0x80);
  });

  it("swallows an autoplay-policy rejection (best-effort prime)", async () => {
    FakeAudio.nextPlayRejection = new Error("NotAllowedError");
    expect(() => primeClipAudio()).not.toThrow();
    await flush();
  });

  it("never clobbers an in-flight clip", async () => {
    const p = playClip(CLIP_URL);
    primeClipAudio();
    const el = FakeAudio.instances[0]!;
    // src untouched — the prime bailed on the active-clip guard.
    expect(el.src).toBe(CLIP_URL);
    el.dispatch("ended");
    await expect(p).resolves.toBeUndefined();
  });

  it("no-ops without window.Audio", () => {
    delete (window as unknown as { Audio?: unknown }).Audio;
    expect(() => primeClipAudio()).not.toThrow();
  });
});

describe("isClipInterrupted", () => {
  it("is false for generic errors and non-Error values", () => {
    expect(isClipInterrupted(new Error("clip-audio: load or decode error"))).toBe(
      false,
    );
    expect(isClipInterrupted("clip_interrupted")).toBe(false);
    expect(isClipInterrupted(null)).toBe(false);
    expect(isClipInterrupted(undefined)).toBe(false);
  });
});

describe("Z4 wire-shape accessors", () => {
  it("readSpokenAudioUrl returns the URL only for a non-empty string", () => {
    expect(readSpokenAudioUrl({ spoken_audio_url: CLIP_URL })).toBe(CLIP_URL);
    expect(readSpokenAudioUrl({ spoken_audio_url: "" })).toBeNull();
    expect(readSpokenAudioUrl({ spoken_audio_url: 42 })).toBeNull();
    expect(readSpokenAudioUrl({})).toBeNull();
    expect(readSpokenAudioUrl(null)).toBeNull();
    expect(readSpokenAudioUrl(undefined)).toBeNull();
  });

  it("readSpokenSetupAudioUrl / readSpokenPunchlineAudioUrl read the joke pair", () => {
    const meta = {
      spoken_audio_setup_url: CLIP_URL,
      spoken_audio_punchline_url: CLIP_URL_2,
    };
    expect(readSpokenSetupAudioUrl(meta)).toBe(CLIP_URL);
    expect(readSpokenPunchlineAudioUrl(meta)).toBe(CLIP_URL_2);
    expect(readSpokenSetupAudioUrl({})).toBeNull();
    expect(readSpokenPunchlineAudioUrl({})).toBeNull();
  });

  it("readSpokenChoiceAudioUrls preserves index alignment, nulling bad slots individually", () => {
    expect(
      readSpokenChoiceAudioUrls({
        spoken_choice_audio_urls: [CLIP_URL, 42, "", CLIP_URL_2],
      }),
    ).toEqual([CLIP_URL, null, null, CLIP_URL_2]);
  });

  it("readSpokenChoiceAudioUrls yields [] for a missing or non-array value", () => {
    expect(readSpokenChoiceAudioUrls({})).toEqual([]);
    expect(
      readSpokenChoiceAudioUrls({ spoken_choice_audio_urls: "nope" }),
    ).toEqual([]);
    expect(readSpokenChoiceAudioUrls(null)).toEqual([]);
  });

  it("effectiveClipUrl gates on the neural-voice flag AND URL presence", () => {
    expect(effectiveClipUrl(true, CLIP_URL)).toBe(CLIP_URL);
    expect(effectiveClipUrl(false, CLIP_URL)).toBeNull();
    expect(effectiveClipUrl(true, null)).toBeNull();
    expect(effectiveClipUrl(true, undefined)).toBeNull();
    expect(effectiveClipUrl(true, "")).toBeNull();
  });
});
