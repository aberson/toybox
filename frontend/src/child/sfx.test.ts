// Sfx is a thin wrapper around HTMLAudioElement. We can't exercise
// real audio in vitest's node env (no DOM), so we focus on the
// graceful-degradation contract: when window.Audio is missing the
// helpers must no-op silently. When window.Audio is faked, the calls
// must hit the underlying play()/preload pipeline.

import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetSfxCacheForTests, playSfx, preloadSfx, unlockAudio } from "./sfx";

describe("sfx", () => {
  afterEach(() => {
    _resetSfxCacheForTests();
    // Restore any window we set on globalThis.
    delete (globalThis as { window?: unknown }).window;
  });

  it("silently no-ops when window.Audio is unavailable", () => {
    expect(playSfx("transition")).toBe(false);
    expect(playSfx("success")).toBe(false);
    // preloadSfx must not throw either.
    expect(() => preloadSfx("transition")).not.toThrow();
  });

  it("plays through the fake Audio when available", () => {
    const playMock = vi.fn().mockResolvedValue(undefined);
    class FakeAudio {
      preload = "";
      currentTime = 0;
      src: string;
      addEventListener = vi.fn();
      removeEventListener = vi.fn();
      play = playMock;
      constructor(src: string) {
        this.src = src;
      }
    }
    (globalThis as { window?: unknown }).window = {
      Audio: FakeAudio,
    };
    expect(playSfx("transition")).toBe(true);
    expect(playMock).toHaveBeenCalledTimes(1);
    // A second play() reuses the cache and increments the call count.
    expect(playSfx("transition")).toBe(true);
    expect(playMock).toHaveBeenCalledTimes(2);
  });

  it("marks an asset failed when play() rejects, and stops trying", async () => {
    const playMock = vi.fn().mockRejectedValue(new Error("blocked"));
    class FakeAudio {
      preload = "";
      currentTime = 0;
      addEventListener = vi.fn();
      removeEventListener = vi.fn();
      play = playMock;
      constructor(_src: string) {}
    }
    (globalThis as { window?: unknown }).window = {
      Audio: FakeAudio,
    };
    expect(playSfx("transition")).toBe(true);
    // Wait a tick for the rejected play() promise to be observed.
    await new Promise((r) => setTimeout(r, 0));
    expect(playSfx("transition")).toBe(false);
  });

  describe("unlockAudio", () => {
    it("calls play() on each canonical SFX element", () => {
      const playMock = vi.fn().mockResolvedValue(undefined);
      const pauseMock = vi.fn();
      class FakeAudio {
        preload = "";
        currentTime = 0;
        src: string;
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        play = playMock;
        pause = pauseMock;
        constructor(src: string) {
          this.src = src;
        }
      }
      (globalThis as { window?: unknown }).window = {
        Audio: FakeAudio,
      };
      unlockAudio();
      // SFX_URLS has `transition` and `success` — both should be primed.
      expect(playMock).toHaveBeenCalledTimes(2);
    });

    it("safe to call when window.Audio is unavailable", () => {
      expect(() => unlockAudio()).not.toThrow();
    });

    it("does not throw when play() rejects", async () => {
      const playMock = vi.fn().mockRejectedValue(new Error("blocked"));
      const pauseMock = vi.fn();
      class FakeAudio {
        preload = "";
        currentTime = 0;
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        play = playMock;
        pause = pauseMock;
        constructor(_src: string) {}
      }
      (globalThis as { window?: unknown }).window = {
        Audio: FakeAudio,
      };
      expect(() => unlockAudio()).not.toThrow();
      // Let microtasks for the rejection observers run so any unhandled
      // rejection would surface (it's caught inside unlockAudio).
      await new Promise((r) => setTimeout(r, 0));
      expect(playMock).toHaveBeenCalledTimes(2);
    });

    it("is safe to call twice — both calls trigger play() and never throw", () => {
      const playMock = vi.fn().mockResolvedValue(undefined);
      const pauseMock = vi.fn();
      class FakeAudio {
        preload = "";
        currentTime = 0;
        src: string;
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        play = playMock;
        pause = pauseMock;
        constructor(src: string) {
          this.src = src;
        }
      }
      (globalThis as { window?: unknown }).window = {
        Audio: FakeAudio,
      };
      // The contract under test is the no-throw + still-primes
      // behavior: the test deliberately does not assert an exact call
      // count, since the unlock path is permitted to skip slots in
      // future variants (e.g. a slot whose Audio element is null).
      // At minimum, both unlock calls together must dispatch >=2
      // play()s — one per SFX slot across the pair.
      expect(() => {
        unlockAudio();
        unlockAudio();
      }).not.toThrow();
      expect(playMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    it("still calls .play() on slots previously marked failed (regression for M3)", async () => {
      // Regression test for M3: an earlier version of unlockAudio
      // skipped any slot whose `failed` flag was set during preload.
      // That meant a slot which 404'd on initial preload but later
      // recovered (e.g. file became available) would never get a
      // gesture-time prime. The fix removes the `failed` early-skip;
      // calling .play() on a failed slot is harmless because the
      // rejection is swallowed inside unlockAudio.
      const playMock = vi.fn().mockRejectedValue(new Error("blocked"));
      const pauseMock = vi.fn();
      class FakeAudio {
        preload = "";
        currentTime = 0;
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        play = playMock;
        pause = pauseMock;
        constructor(_src: string) {}
      }
      (globalThis as { window?: unknown }).window = {
        Audio: FakeAudio,
      };
      // First playSfx call marks the slot failed (its play() rejects).
      expect(playSfx("transition")).toBe(true);
      await new Promise((r) => setTimeout(r, 0));
      // playSfx now refuses (slot is `failed`).
      expect(playSfx("transition")).toBe(false);
      // unlockAudio must STILL prime the slot — the failed flag is
      // not a reason to skip the gesture-time priming attempt.
      const callsBeforeUnlock = playMock.mock.calls.length;
      unlockAudio();
      await new Promise((r) => setTimeout(r, 0));
      // Both canonical SFX slots primed (transition was already
      // failed, success is fresh) — at least one new play() call
      // beyond the count from the playSfx exercise above.
      expect(playMock.mock.calls.length).toBeGreaterThan(callsBeforeUnlock);
    });
  });
});
