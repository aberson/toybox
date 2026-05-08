// Screen Wake Lock — keeps the iPad display awake during an active kiosk activity.
// Feature-detected; silent no-op on browsers without the API (older iPadOS, desktop Safari).
// Documented at https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API.

export interface WakeLockSentinel {
  released: boolean;
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
}

interface NavigatorWakeLock {
  request: (type: "screen") => Promise<WakeLockSentinel>;
}

function getApi(): NavigatorWakeLock | null {
  if (typeof navigator === "undefined") return null;
  const nav = navigator as Navigator & { wakeLock?: NavigatorWakeLock };
  return nav.wakeLock ?? null;
}

// Acquire a screen wake lock. Returns the sentinel on success, or null
// when the API is missing (older iPadOS, desktop Safari) or the browser
// rejects the request (permissions policy, document not visible, etc.).
// Stateless on purpose — the caller owns the sentinel reference and is
// responsible for releasing it. Concurrent-acquire de-duplication lives
// in the orchestration layer (see App.tsx `acquireIfNeeded`), not here.
export async function acquireWakeLock(): Promise<WakeLockSentinel | null> {
  const api = getApi();
  if (api === null) return null;
  try {
    return await api.request("screen");
  } catch {
    return null;
  }
}

// Release a held sentinel. Idempotent and silent: passing null is a
// no-op, and calling .release() on an already-released sentinel still
// succeeds (or rejects, which we swallow). Safe to call from cleanup
// paths without first checking `sentinel.released`.
export async function releaseWakeLock(
  sentinel: WakeLockSentinel | null,
): Promise<void> {
  if (sentinel === null) return;
  try {
    await sentinel.release();
  } catch {
    // Already released or browser-rejected — idempotent silent no-op.
  }
}

// Helper that watches visibility and re-acquires on visible-after-system-release.
// Returns a cleanup function. Caller passes a getter for the current sentinel,
// a setter for the refreshed one, and a wantLock predicate so this module owns
// no module-scoped state. The wantLock check protects against the watcher
// reacquiring while the activity is terminal/idle (the lock would linger
// across the "All done!" screen) — e.g. user backgrounds during play and
// foregrounds again after the activity has completed in the background.
export function watchVisibilityForReacquire(
  getSentinel: () => WakeLockSentinel | null,
  setSentinel: (s: WakeLockSentinel | null) => void,
  getWantLock: () => boolean,
): () => void {
  if (typeof document === "undefined") return () => {};
  const handler = (): void => {
    if (document.visibilityState !== "visible") return;
    if (!getWantLock()) return;
    const cur = getSentinel();
    // Re-acquire if we never had one, or the system released it (sentinel.released = true).
    if (cur === null || cur.released) {
      void acquireWakeLock().then(setSentinel);
    }
  };
  document.addEventListener("visibilitychange", handler);
  return () => document.removeEventListener("visibilitychange", handler);
}
