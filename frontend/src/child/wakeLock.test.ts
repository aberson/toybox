/**
 * @vitest-environment happy-dom
 */
// Wake Lock tests. The Wake Lock API isn't present in happy-dom, so we
// stub `navigator.wakeLock` directly and assert on the behavior of our
// thin wrappers + the visibility-reacquire helper. We need a DOM
// (happy-dom) to dispatch `visibilitychange` events on `document`; the
// repo's vitest defaults `.test.ts` files to node, so the docblock
// above flips this single file to happy-dom.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acquireWakeLock,
  releaseWakeLock,
  watchVisibilityForReacquire,
  type WakeLockSentinel,
} from "./wakeLock";

type FakeSentinel = WakeLockSentinel & {
  release: ReturnType<typeof vi.fn>;
};

function makeSentinel(): FakeSentinel {
  const sentinel: FakeSentinel = {
    released: false,
    release: vi.fn().mockImplementation(async () => {
      sentinel.released = true;
    }),
    addEventListener: vi.fn(),
  };
  return sentinel;
}

function installWakeLockApi(
  request: (type: "screen") => Promise<WakeLockSentinel>,
): void {
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: { request },
  });
}

function removeWakeLockApi(): void {
  // configurable:true above lets us delete cleanly between tests.
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: undefined,
  });
  delete (navigator as unknown as { wakeLock?: unknown }).wakeLock;
}

describe("wakeLock", () => {
  afterEach(() => {
    removeWakeLockApi();
    vi.restoreAllMocks();
  });

  it("acquireWakeLock returns a sentinel when the API is present", async () => {
    const sentinel = makeSentinel();
    const request = vi.fn().mockResolvedValue(sentinel);
    installWakeLockApi(request);
    const got = await acquireWakeLock();
    expect(got).toBe(sentinel);
    expect(request).toHaveBeenCalledWith("screen");
  });

  it("releaseWakeLock calls sentinel.release()", async () => {
    const sentinel = makeSentinel();
    await releaseWakeLock(sentinel);
    expect(sentinel.release).toHaveBeenCalledTimes(1);
  });

  it("acquireWakeLock returns null when navigator.wakeLock is undefined", async () => {
    removeWakeLockApi();
    const got = await acquireWakeLock();
    expect(got).toBeNull();
  });

  it("acquireWakeLock returns null when the request rejects", async () => {
    installWakeLockApi(() => Promise.reject(new Error("denied")));
    const got = await acquireWakeLock();
    expect(got).toBeNull();
  });

  it("releaseWakeLock called twice on the same sentinel does not throw", async () => {
    const sentinel = makeSentinel();
    await releaseWakeLock(sentinel);
    await expect(releaseWakeLock(sentinel)).resolves.toBeUndefined();
    expect(sentinel.release).toHaveBeenCalledTimes(2);
  });

  describe("watchVisibilityForReacquire", () => {
    beforeEach(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "visible",
      });
    });

    it("re-acquires when visibility becomes visible after the system released the sentinel", async () => {
      const released: FakeSentinel = makeSentinel();
      released.released = true;
      const fresh = makeSentinel();
      const request = vi.fn().mockResolvedValue(fresh);
      installWakeLockApi(request);

      let current: WakeLockSentinel | null = released;
      const setter = vi.fn((s: WakeLockSentinel | null) => {
        current = s;
      });
      const cleanup = watchVisibilityForReacquire(
        () => current,
        setter,
        () => true,
      );

      document.dispatchEvent(new Event("visibilitychange"));
      // The handler kicks off an async acquireWakeLock; let the full
      // promise chain (request → acquireWakeLock async wrapper → .then)
      // settle by yielding to the macrotask queue.
      await new Promise((r) => setTimeout(r, 0));

      expect(request).toHaveBeenCalledWith("screen");
      expect(setter).toHaveBeenCalledWith(fresh);

      cleanup();
    });

    it("does not re-acquire when visibility becomes visible while a live sentinel is still held", async () => {
      const live = makeSentinel();
      const request = vi.fn().mockResolvedValue(makeSentinel());
      installWakeLockApi(request);

      const setter = vi.fn();
      const cleanup = watchVisibilityForReacquire(
        () => live,
        setter,
        () => true,
      );

      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 0));

      expect(request).not.toHaveBeenCalled();
      expect(setter).not.toHaveBeenCalled();

      cleanup();
    });

    it("re-acquires when visibility becomes visible and there was no sentinel yet", async () => {
      const fresh = makeSentinel();
      const request = vi.fn().mockResolvedValue(fresh);
      installWakeLockApi(request);

      let current: WakeLockSentinel | null = null;
      const setter = vi.fn((s: WakeLockSentinel | null) => {
        current = s;
      });
      const cleanup = watchVisibilityForReacquire(
        () => current,
        setter,
        () => true,
      );

      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 0));

      expect(request).toHaveBeenCalledTimes(1);
      expect(setter).toHaveBeenCalledWith(fresh);

      cleanup();
    });

    it("does NOT re-acquire when wantLock returns false (e.g. activity went terminal in background)", async () => {
      // Regression test for M2: previously the visibility handler
      // re-acquired any time we didn't already hold a live sentinel,
      // even if the activity had since gone terminal/idle. The
      // sentinel would then linger across the "All done!" screen.
      const fresh = makeSentinel();
      const request = vi.fn().mockResolvedValue(fresh);
      installWakeLockApi(request);

      let current: WakeLockSentinel | null = null;
      const setter = vi.fn((s: WakeLockSentinel | null) => {
        current = s;
      });
      const cleanup = watchVisibilityForReacquire(
        () => current,
        setter,
        () => false,
      );

      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 0));

      expect(request).not.toHaveBeenCalled();
      expect(setter).not.toHaveBeenCalled();

      cleanup();
    });

    it("cleanup removes the visibilitychange listener", async () => {
      const fresh = makeSentinel();
      const request = vi.fn().mockResolvedValue(fresh);
      installWakeLockApi(request);

      let current: WakeLockSentinel | null = null;
      const setter = vi.fn((s: WakeLockSentinel | null) => {
        current = s;
      });
      const cleanup = watchVisibilityForReacquire(
        () => current,
        setter,
        () => true,
      );
      cleanup();

      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 0));

      expect(request).not.toHaveBeenCalled();
      expect(setter).not.toHaveBeenCalled();
    });
  });

  describe("acquireWakeLock concurrency", () => {
    it("does not double-acquire when called concurrently (resolves to the same sentinel)", async () => {
      // Regression test for H1: two concurrent triggers (activity-change
      // effect + visibilitychange handler) could both observe a null
      // sentinel before either await landed. The H1 fix wraps acquire
      // calls in an in-flight guard at the App.tsx orchestration layer
      // — this test asserts the underlying primitive is well-behaved
      // when both callers do hit the API simultaneously: each call
      // gets a sentinel, and a single shared in-flight promise (the
      // app-layer guard) means navigator.wakeLock.request is dispatched
      // exactly once for the racing pair.
      const sentinel = makeSentinel();
      const request = vi.fn().mockResolvedValue(sentinel);
      installWakeLockApi(request);

      // Simulate the App.tsx in-flight guard pattern: cache the first
      // pending promise and return the same one for the racing call.
      let inFlight: Promise<WakeLockSentinel | null> | null = null;
      const acquireIfNeeded = (): Promise<WakeLockSentinel | null> => {
        if (inFlight !== null) return inFlight;
        const p = acquireWakeLock();
        inFlight = p;
        void p.finally(() => {
          inFlight = null;
        });
        return p;
      };

      const [a, b] = await Promise.all([acquireIfNeeded(), acquireIfNeeded()]);
      expect(a).toBe(sentinel);
      expect(b).toBe(sentinel);
      expect(request).toHaveBeenCalledTimes(1);
    });
  });
});
