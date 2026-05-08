// Tiny SFX preloader for the kiosk. We expose two named effects:
// `transition` (fired when the active step changes) and `success`
// (fired when the activity reaches `completed`/`ended`).
//
// v1 is a silence-stub: the actual .wav files are sourced in M4. If
// the audio element returns 404 or fails to load we silently no-op so
// the kiosk doesn't show a console error every transition. The
// `playSfx` and `preloadSfx` helpers are safe to call in non-DOM
// environments (they bail when `window`/`Audio` are missing) which
// also keeps test code simple.

export type SfxName = "transition" | "success";

export interface SfxAsset {
  url: string;
  audio: HTMLAudioElement | null;
  // Once a load failure happens we mark the slot dead so subsequent
  // playSfx calls are pure no-ops — we don't want to spam reloads.
  failed: boolean;
}

const SFX_URLS: Record<SfxName, string> = {
  transition: "/sfx/transition.wav",
  success: "/sfx/success.wav",
};

const cache: Partial<Record<SfxName, SfxAsset>> = {};

function hasAudio(): boolean {
  return typeof window !== "undefined" && typeof window.Audio === "function";
}

function getOrCreate(name: SfxName): SfxAsset | null {
  if (!hasAudio()) return null;
  const existing = cache[name];
  if (existing) return existing;
  const url = SFX_URLS[name];
  let audio: HTMLAudioElement | null = null;
  try {
    audio = new window.Audio(url);
    audio.preload = "auto";
  } catch {
    audio = null;
  }
  const asset: SfxAsset = { url, audio, failed: audio === null };
  if (audio !== null) {
    audio.addEventListener("error", () => {
      asset.failed = true;
    });
  }
  cache[name] = asset;
  return asset;
}

// Preload (force the browser to fetch the asset). Safe to call
// repeatedly — the cache makes it idempotent. If the file is missing
// the asset is marked failed and subsequent plays are no-ops.
export function preloadSfx(name: SfxName): void {
  getOrCreate(name);
}

// Play a named effect. Always swallows errors — the kiosk should not
// be derailed by an audio glitch. Returns whether playback was
// dispatched (mostly useful for tests).
export function playSfx(name: SfxName): boolean {
  const asset = getOrCreate(name);
  if (asset === null || asset.failed || asset.audio === null) return false;
  try {
    // Reset cursor so rapid back-to-back transitions still trigger.
    asset.audio.currentTime = 0;
    const promise = asset.audio.play();
    if (
      typeof promise === "object" &&
      promise !== null &&
      typeof (promise as Promise<void>).catch === "function"
    ) {
      (promise as Promise<void>).catch(() => {
        // Browsers reject play() if autoplay is blocked or the file
        // failed to load. Mark the slot failed so we stop trying.
        asset.failed = true;
      });
    }
    return true;
  } catch {
    asset.failed = true;
    return false;
  }
}

// iOS Safari only allows audio playback after a user gesture has
// "unlocked" each Audio element. Calling play() inside the gesture
// (then immediately pausing) is the standard idiom — once unlocked,
// later programmatic plays succeed silently. Idempotent BY DESIGN:
// callers don't need to track state, and a guard would break the
// page-reload-with-cached-token edge case where the kiosk skips the
// PIN prompt but audio still needs priming on the first user gesture.
export function unlockAudio(): void {
  if (!hasAudio()) return;
  // Iterate the canonical SFX list rather than just-cached entries to
  // guarantee both `transition` and `success` are unlocked together
  // on the first PIN submit.
  for (const name of Object.keys(SFX_URLS) as SfxName[]) {
    const asset = getOrCreate(name);
    // Note: we deliberately do NOT skip slots whose `failed` flag is
    // set. unlockAudio is a best-effort gesture-time primer; calling
    // .play() on a previously-failed slot is harmless (the rejection
    // is swallowed below) and the slot may have recovered (e.g. the
    // file finished loading after the initial preload error). The
    // slot is only skipped when there is literally nothing to play
    // on (asset null, or the Audio element couldn't be constructed).
    if (asset === null || asset.audio === null) continue;
    try {
      const promise = asset.audio.play();
      if (
        typeof promise === "object" &&
        promise !== null &&
        typeof (promise as Promise<void>).catch === "function"
      ) {
        (promise as Promise<void>)
          .then(() => {
            // Pause immediately after play() resolves — the play call
            // is what unlocks iOS audio for that element; pausing
            // keeps the kiosk silent until a real playSfx call.
            asset.audio?.pause();
          })
          .catch(() => {
            // Autoplay-block / 404 / other rejection — silent no-op.
            // Do NOT mark failed=true: a future playSfx after a real
            // user gesture may still succeed. The unlock idiom is a
            // best-effort prime, not a load test.
          });
      }
    } catch {
      // Synchronous exception (very rare) — silent no-op.
    }
  }
}

// Test seam: clear the cache so a fresh `getOrCreate` runs. Real
// callers never need this; vitest uses it to isolate cases.
export function _resetSfxCacheForTests(): void {
  for (const k of Object.keys(cache) as SfxName[]) {
    delete cache[k];
  }
}
