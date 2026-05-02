// Sfx is a thin wrapper around HTMLAudioElement. We can't exercise
// real audio in vitest's node env (no DOM), so we focus on the
// graceful-degradation contract: when window.Audio is missing the
// helpers must no-op silently. When window.Audio is faked, the calls
// must hit the underlying play()/preload pipeline.

import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetSfxCacheForTests, playSfx, preloadSfx } from "./sfx";

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
});
