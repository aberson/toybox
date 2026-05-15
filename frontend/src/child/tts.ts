// Phase K Step K8 — Kiosk Text-To-Speech substrate.
//
// Thin wrapper around ``window.speechSynthesis`` that the kiosk's
// click-to-read (K9) and joke-step delivery (K12) layers will call.
// Three contracts that justify this module's existence:
//
//   1. iOS-PWA gesture unlock. Mobile Safari silently no-ops
//      ``speechSynthesis.speak`` outside of a user-gesture call stack.
//      The first ``speak()`` invocation inside a gesture flips the
//      module's ``unlocked`` flag; subsequent calls work regardless of
//      whether they originate from a gesture stack (within the same
//      session). The state machine is best-effort — if the first call
//      happens outside a gesture we still attempt the utterance and
//      mark unlocked optimistically; the next user-tap will retry.
//
//   2. Voice-list bootstrapping race. Chrome (and some other engines)
//      return an empty list from ``speechSynthesis.getVoices()`` on
//      first-load; the real list arrives via the ``voiceschanged``
//      event. We attach a one-shot listener at module load and cache
//      the result so consumer lookups are synchronous.
//
//   3. Graceful degradation in non-DOM test environments. Vitest
//      defaults this kiosk module to the node environment (see
//      ``vitest.config.ts``) so ``window`` is undefined unless a test
//      stages it. Every entry point checks for the API before touching
//      it; missing API → silent no-op (matches ``sfx.ts``'s pattern).
//
// **No callers in K8.** K9 wires word taps + Read Me button; K12 wires
// joke setup/punchline auto-play. This file is the substrate only.

export interface VoiceProfile {
  // Speaking rate. Browser-clamped to roughly [0.1, 10] but Phase K's
  // VoiceProfile schema (``src/toybox/personas/models.py``) constrains
  // authored profiles to [0.5, 2.0]; this module passes whatever it
  // gets through unmodified so a future bound change is a one-line edit
  // on the producer side.
  rate: number;
  // Pitch multiplier. Same range story as rate — schema bounds
  // [0.0, 2.0], we don't enforce here.
  pitch: number;
  // Optional named voice from ``speechSynthesis.getVoices()``. When
  // unset OR not found in the bootstrapped list, ``speak()`` falls
  // back to the engine's default voice — never throws.
  voiceName?: string;
}

// Internal module state. We keep these as closure-local lets rather
// than exports so callers can't reach in and reset them outside of
// the explicit test seam below.
let unlocked = false;
let voicesCache: SpeechSynthesisVoice[] = [];
let voiceListenerAttached = false;

function hasSynthesis(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.speechSynthesis !== "undefined" &&
    typeof window.SpeechSynthesisUtterance === "function"
  );
}

// Refresh the voice cache from the live API. Called eagerly the first
// time the module touches speechSynthesis AND from the ``voiceschanged``
// event handler.
function refreshVoicesCache(): void {
  if (!hasSynthesis()) return;
  try {
    const v = window.speechSynthesis.getVoices();
    if (Array.isArray(v)) {
      voicesCache = v;
    }
  } catch {
    // Some embedded engines throw on early getVoices(); leave cache as is.
  }
}

// Attach the one-shot voiceschanged listener so a delayed voice list
// arrival populates the cache without per-call polling. Idempotent —
// we track attachment so module-reset for tests stays clean.
function ensureVoiceListenerAttached(): void {
  if (!hasSynthesis() || voiceListenerAttached) return;
  const synth = window.speechSynthesis;
  // Some implementations expose addEventListener; older Safari only
  // exposes ``onvoiceschanged``. Prefer addEventListener for cleanup,
  // fall back to property assignment so older surfaces still wire up.
  if (typeof synth.addEventListener === "function") {
    synth.addEventListener("voiceschanged", refreshVoicesCache);
  } else {
    (synth as unknown as { onvoiceschanged?: () => void }).onvoiceschanged =
      refreshVoicesCache;
  }
  voiceListenerAttached = true;
  // Prime synchronously too — most engines return the list on first
  // call after the page has been alive for a tick.
  refreshVoicesCache();
}

/**
 * True when the iOS-PWA gesture-unlock flag has been set. After the
 * first ``speak()`` call inside a user gesture, subsequent ``speak()``
 * calls work outside a gesture stack (within the same session).
 *
 * Used by K9 / K12 surfaces that need to know whether a programmatic
 * speak is safe before invoking it. v1 callers can ignore this and
 * just call ``speak()`` — the unlock is best-effort and silent.
 */
export function isUnlocked(): boolean {
  return unlocked;
}

/**
 * Speak ``text`` using ``profile``. Returns a Promise that resolves
 * when the utterance completes (``onend``) and rejects on error
 * (``onerror``). When the synthesis API is unavailable, resolves
 * immediately — silent degradation, same pattern as ``sfx.ts``.
 *
 * The first call inside a user gesture marks the module unlocked; once
 * unlocked, later calls succeed regardless of gesture context (within
 * the same Safari session). We DO NOT gate on ``unlocked`` ourselves —
 * any caller invoking speak() is signaling intent, and we want the
 * native API to make the gesture decision so future Safari quirks
 * (e.g. a relaxation of the rule) take effect without a code change.
 *
 * Voice-name fallback: if ``profile.voiceName`` is set but the engine
 * doesn't expose a matching voice, we leave ``utterance.voice = null``
 * so the engine picks its default. We never throw on a missing voice —
 * a stale profile from a persona authored against a different
 * device's voice list should not break the kiosk.
 */
export function speak(text: string, profile: VoiceProfile): Promise<void> {
  if (!hasSynthesis()) return Promise.resolve();
  ensureVoiceListenerAttached();

  return new Promise<void>((resolve, reject) => {
    let utterance: SpeechSynthesisUtterance;
    try {
      utterance = new window.SpeechSynthesisUtterance(text);
    } catch (err) {
      // Constructor failure (very rare; some headless engines throw
      // on empty/invalid text). Reject so the caller can surface it
      // if they want, but DO NOT mark the unlock flag — we never got
      // far enough to count as a gesture-time speak attempt.
      reject(err);
      return;
    }
    utterance.rate = profile.rate;
    utterance.pitch = profile.pitch;
    if (profile.voiceName !== undefined && profile.voiceName !== "") {
      // refreshVoicesCache may already have run via the listener; if not,
      // pull synchronously so a same-tick voice lookup still works.
      if (voicesCache.length === 0) {
        refreshVoicesCache();
      }
      const match = voicesCache.find((v) => v.name === profile.voiceName);
      if (match !== undefined) {
        utterance.voice = match;
      }
      // If no match: leave ``utterance.voice`` unset and let the engine
      // pick its default. Intentionally no throw — see docstring.
    }
    utterance.onend = (): void => {
      resolve();
    };
    utterance.onerror = (ev): void => {
      // Reject with an Error so callers can ``.catch`` deterministically.
      // The SpeechSynthesisErrorEvent's ``error`` field is the most
      // useful payload (e.g. "interrupted" when cancel() fired).
      const errName =
        typeof ev === "object" && ev !== null && "error" in ev
          ? String((ev as { error?: unknown }).error)
          : "speech_error";
      reject(new Error(`speechSynthesis: ${errName}`));
    };

    try {
      window.speechSynthesis.speak(utterance);
      // Set the unlock flag AFTER speak() has been dispatched without
      // a synchronous throw. The actual no-op-outside-gesture behavior
      // is engine-internal; from the JS side we can only observe that
      // the speak call didn't throw and assume best-effort.
      unlocked = true;
    } catch (err) {
      reject(err);
    }
  });
}

/**
 * Interrupt any in-flight speech. Safe to call when nothing is
 * speaking — ``speechSynthesis.cancel()`` is itself idempotent.
 * Pending Promises from outstanding ``speak()`` calls will reject
 * with a "interrupted"-style error via the utterance's ``onerror``.
 */
export function cancel(): void {
  if (!hasSynthesis()) return;
  try {
    window.speechSynthesis.cancel();
  } catch {
    // Some engines throw if cancel() is called before any speak()
    // primed the queue. Swallow — caller's UX is unaffected.
  }
}

/**
 * Test seam. Resets module-internal state so a fresh test can stage
 * a different ``window.speechSynthesis`` mock without bleed-through.
 * Production code never calls this.
 */
export function _resetTtsStateForTests(): void {
  unlocked = false;
  voicesCache = [];
  voiceListenerAttached = false;
}
