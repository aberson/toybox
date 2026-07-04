// Phase Z Z5 — Kiosk voice-clip playback substrate.
//
// ONE shared ``HTMLAudioElement`` plays the server-rendered neural TTS
// clips (Z3/Z4: Kokoro WAVs under ``/api/static/tts/<voice>/<sha16>.wav``).
// A single element — not per-clip ``new Audio(url)`` — because iOS
// Safari's gesture unlock is PER-ELEMENT (the sfx.ts lesson): priming
// this one element inside the PIN-gate gesture unlocks every later
// ``src`` swap, whereas a fresh element per clip would need a fresh
// gesture per clip.
//
// Contracts:
//
//   1. ``playClip(url)`` resolves when playback ENDS and rejects on
//      404/network, decode error, autoplay-policy rejection, or
//      interruption (another ``playClip``/``stopClip`` call). Callers
//      use rejection to fall back to the Web Speech path (tts.ts) —
//      a 404 is DESIGNED behavior (the Z4 background worker may not
//      have rendered the clip yet), not an error to surface.
//
//   2. Interruption rejections are distinguishable via
//      ``isClipInterrupted``. Callers must NOT fall back to Web Speech
//      on interruption — an interrupt means another surface took audio
//      focus on purpose, and a fallback utterance would talk over it.
//
//   3. Single audio focus, clip side: ``playClip`` cancels any
//      in-flight Web Speech via tts.ts ``cancel()`` (whose #207
//      in-flight guard is preserved — we call it, never reimplement
//      it). The speech side of the same coin lives with the callers:
//      every surface's Web-Speech path calls ``stopClip()`` before
//      ``speak()`` so a playing clip never talks over an utterance.
//
//   4. Graceful degradation in non-DOM test environments: missing
//      ``window.Audio`` → ``playClip`` REJECTS (so the caller's
//      fallback chain still lands on Web Speech, which itself no-ops
//      silently — see tts.ts) and the other entry points no-op.
//
// Z9 UAT note: the gesture prime below is best-effort and only real
// hardware can prove it — the iPad UAT must verify a JokeStep AUTOPLAY
// clip plays without a preceding tap (tap-triggered surfaces are
// in-gesture anyway, so autoplay is the exposed case).

import { cancel } from "./tts";

// Rejection marker for interruptions (contract 2). A string constant
// compared via Error.message — cheap, structural, and survives the
// vi.mock boundary (an Error subclass would not instanceof-match when
// the module is mocked per-test-file). Exported so tests build
// interruption rejections from the SAME marker ``isClipInterrupted``
// matches (one source of truth; the test files' partial mocks spread
// the actual module, so the export rides through them).
export const CLIP_INTERRUPTED_MESSAGE = "clip_interrupted";

// Silent WAV for the gesture-time prime, built programmatically so the
// shape is auditable: 8 kHz mono 8-bit unsigned PCM carrying REAL
// silent samples (0x80 is the 8-bit zero point), ~50 ms. A zero-length
// data chunk (duration 0) is exactly the edge where iOS unlock
// payloads historically fail (the Howler/unmute.js lesson — both ship
// actual silent frames), and a rejected prime would silently degrade
// EVERY autoplay clip on the device. Inline ``data:`` URI so priming
// needs no network fetch and cannot 404 — the play-then-pause idiom
// itself mirrors sfx.ts ``unlockAudio``.
const PRIME_SILENCE_SAMPLE_RATE = 8_000;
const PRIME_SILENCE_SAMPLE_COUNT = 400; // 400 samples @ 8 kHz ≈ 50 ms

function buildSilentWavDataUri(): string {
  // 8-bit mono → one byte per sample.
  const dataSize = PRIME_SILENCE_SAMPLE_COUNT;
  const bytes = new Uint8Array(44 + dataSize);
  const view = new DataView(bytes.buffer);
  const writeAscii = (offset: number, s: string): void => {
    for (let i = 0; i < s.length; i++) {
      bytes[offset + i] = s.charCodeAt(i);
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true); // RIFF payload size
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size (PCM)
  view.setUint16(20, 1, true); // audio format: PCM
  view.setUint16(22, 1, true); // channels: mono
  view.setUint32(24, PRIME_SILENCE_SAMPLE_RATE, true);
  view.setUint32(28, PRIME_SILENCE_SAMPLE_RATE, true); // byte rate (1 B/sample)
  view.setUint16(32, 1, true); // block align
  view.setUint16(34, 8, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, dataSize, true);
  bytes.fill(0x80, 44); // true silence frames, not an empty chunk
  let binary = "";
  for (const b of bytes) {
    binary += String.fromCharCode(b);
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

const SILENT_WAV_DATA_URI = buildSilentWavDataUri();

// The in-flight clip: a single settle function. Only one clip can play
// at a time on the one shared element, so module state is a single
// slot, not a queue — a new playClip settles (rejects) the old slot.
interface ActiveClip {
  finish: (err: Error | null) => void;
}

let sharedAudio: HTMLAudioElement | null = null;
let activeClip: ActiveClip | null = null;

function hasAudio(): boolean {
  return typeof window !== "undefined" && typeof window.Audio === "function";
}

function getOrCreateElement(): HTMLAudioElement | null {
  if (!hasAudio()) return null;
  if (sharedAudio !== null) return sharedAudio;
  try {
    const el = new window.Audio();
    el.preload = "auto";
    sharedAudio = el;
  } catch {
    // Constructor failure (embedded/partial engines). Leave null so
    // playClip rejects and callers fall back to Web Speech.
    sharedAudio = null;
  }
  return sharedAudio;
}

/**
 * True when ``err`` is a playClip rejection caused by interruption
 * (another ``playClip`` or a ``stopClip`` took the shared element).
 * Callers skip their Web-Speech fallback for these — the interrupting
 * action owns the audio focus now (contract 2 above).
 */
export function isClipInterrupted(err: unknown): boolean {
  return err instanceof Error && err.message === CLIP_INTERRUPTED_MESSAGE;
}

// Settle the in-flight clip (if any) with an interruption rejection
// and pause the element so the audio actually stops. Shared by
// playClip (steal focus) and stopClip (drop focus).
function interruptActiveClip(): void {
  const entry = activeClip;
  if (entry !== null) {
    entry.finish(new Error(CLIP_INTERRUPTED_MESSAGE));
  }
  if (sharedAudio !== null) {
    try {
      sharedAudio.pause();
    } catch {
      // Some engines throw on pause() before any load — a failed pause
      // must not break the caller (the new clip's src swap stops the
      // old playback anyway).
    }
  }
}

/**
 * Prime the shared element inside a user gesture. Called from the
 * kiosk PIN-gate submit handler (KioskPinPrompt) right next to
 * ``sfx.ts unlockAudio()`` — iOS unlock is per-element, so the SFX
 * unlock does NOT cover this element. Plays a silent inline WAV and
 * pauses on success; every failure path is swallowed (best-effort
 * prime, same posture as unlockAudio). Idempotent BY DESIGN — see
 * unlockAudio's rationale for not guarding on a "primed" flag.
 */
export function primeClipAudio(): void {
  const el = getOrCreateElement();
  if (el === null) return;
  // Never clobber a real clip. Priming happens at the PIN gate, before
  // any activity exists, so this guard is purely defensive.
  if (activeClip !== null) return;
  try {
    el.src = SILENT_WAV_DATA_URI;
    const promise = el.play();
    if (
      typeof promise === "object" &&
      promise !== null &&
      typeof (promise as Promise<void>).catch === "function"
    ) {
      (promise as Promise<void>)
        .then(() => {
          // Play resolved — the element is unlocked for this session.
          // Pause immediately so the kiosk stays silent (the source is
          // silent anyway; the pause just releases the media session).
          // Guarded on activeClip: a nonconformant engine that resolves
          // this prime's play() LATE (after a real clip has started on
          // the shared element) must not pause the kid's clip.
          if (activeClip === null) {
            el.pause();
          }
        })
        .catch(() => {
          // Autoplay-block outside a gesture / decode quirk — silent
          // no-op. A later in-gesture playClip may still succeed.
        });
    }
  } catch {
    // Synchronous engine exception — silent no-op (best-effort prime).
  }
}

/**
 * Play ``url`` on the shared element. Resolves when playback ends;
 * rejects on load/decode error (404s surface here), autoplay-policy
 * rejection, or interruption (see ``isClipInterrupted``).
 *
 * Takes audio focus: cancels in-flight Web Speech (tts.ts cancel, #207
 * guard intact) and interrupts any previous clip before starting.
 */
export function playClip(url: string): Promise<void> {
  const el = getOrCreateElement();
  if (el === null) {
    // Non-DOM environment or constructor failure — reject so the
    // caller's fallback chain lands on Web Speech (which silently
    // no-ops without a DOM; the degrade stays coherent end-to-end).
    return Promise.reject(new Error("clip-audio: Audio unavailable"));
  }
  // Single audio focus (contract 3): a clip starting means any
  // in-flight utterance stops, and any previous clip's promise settles
  // as interrupted BEFORE the new playback begins.
  cancel();
  interruptActiveClip();

  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const onEnded = (): void => {
      entry.finish(null);
    };
    const onError = (): void => {
      // The element's error event covers 404/network AND decode
      // failures — the SongPlayer 404-grace precedent. The caller
      // falls back to Web Speech; a not-yet-rendered clip is expected.
      entry.finish(new Error("clip-audio: load or decode error"));
    };
    const entry: ActiveClip = {
      finish: (err: Error | null): void => {
        if (settled) return;
        settled = true;
        el.removeEventListener("ended", onEnded);
        el.removeEventListener("error", onError);
        if (activeClip === entry) {
          activeClip = null;
        }
        if (err === null) {
          resolve();
        } else {
          reject(err);
        }
      },
    };
    el.addEventListener("ended", onEnded);
    el.addEventListener("error", onError);
    activeClip = entry;
    try {
      el.src = url;
      const promise = el.play();
      if (
        typeof promise === "object" &&
        promise !== null &&
        typeof (promise as Promise<void>).catch === "function"
      ) {
        (promise as Promise<void>).catch((err: unknown) => {
          // Autoplay-policy rejection (no gesture / prime didn't take)
          // or an early load abort. If the clip was already interrupted
          // the finish below is a no-op (settled flag).
          entry.finish(
            err instanceof Error
              ? err
              : new Error("clip-audio: play() rejected"),
          );
        });
      }
    } catch (err) {
      entry.finish(
        err instanceof Error ? err : new Error("clip-audio: play() threw"),
      );
    }
  });
}

/**
 * Stop the current clip (if any): pauses the element and rejects the
 * in-flight ``playClip`` promise as interrupted. Safe to call when
 * nothing is playing — it becomes a no-op. Callers invoke this before
 * starting Web Speech (the speech half of single audio focus).
 * ``JokeStep`` additionally calls it on unmount (mirroring its unmount
 * ``cancel()``); the tap surfaces (ReadMeButton / ChoiceReadButton)
 * deliberately have NO unmount hook, so a body clip keeps playing
 * across a step advance — known parity behavior: pre-Z5 Web Speech was
 * likewise never cancelled on advance, and the next tap or clip takes
 * focus anyway.
 */
export function stopClip(): void {
  interruptActiveClip();
}

// ---------------------------------------------------------------------------
// Z4 wire-shape accessors.
//
// The backend writes clip URLs into step ``metadata_json`` at enqueue
// time (producer publishes, consumer reads — the kiosk never derives
// cache keys). These readers are the ONE place the key strings live on
// the frontend (code-quality: one source of truth for shape constants);
// StepCard and RewardStep both import them rather than repeating the
// literals. Every reader is defensive per the K12 "render even on a
// malformed envelope" contract: wrong type / empty string / absent →
// null, never a throw on the kiosk render path.
// ---------------------------------------------------------------------------

type StepMetadata = Record<string, unknown> | null | undefined;

function readUrlKey(metadata: StepMetadata, key: string): string | null {
  if (typeof metadata !== "object" || metadata === null) return null;
  const v = metadata[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Plain step body clip (``spoken_audio_url``). */
export function readSpokenAudioUrl(metadata: StepMetadata): string | null {
  return readUrlKey(metadata, "spoken_audio_url");
}

/** Joke setup clip (``spoken_audio_setup_url``; incl. reward jokes). */
export function readSpokenSetupAudioUrl(metadata: StepMetadata): string | null {
  return readUrlKey(metadata, "spoken_audio_setup_url");
}

/** Joke punchline clip (``spoken_audio_punchline_url``; incl. reward jokes). */
export function readSpokenPunchlineAudioUrl(
  metadata: StepMetadata,
): string | null {
  return readUrlKey(metadata, "spoken_audio_punchline_url");
}

/**
 * Choice-label clips (``spoken_choice_audio_urls``) — a list aligned
 * index-for-index with the step's ``choices``. Malformed entries
 * collapse to ``null`` PER SLOT (not a whole-list drop) so one bad
 * entry doesn't strip clips from the sibling choices; a missing or
 * non-array value yields ``[]`` (every choice falls back).
 */
export function readSpokenChoiceAudioUrls(
  metadata: StepMetadata,
): (string | null)[] {
  if (typeof metadata !== "object" || metadata === null) return [];
  const v = metadata["spoken_choice_audio_urls"];
  if (!Array.isArray(v)) return [];
  return v.map((entry) =>
    typeof entry === "string" && entry.length > 0 ? entry : null,
  );
}

/**
 * The clip URL a surface should actually attempt: ``null`` when the
 * neural-voice gate is off OR the URL is absent/blank. Shared by every
 * Z5 surface so the "flag off means no clip attempts" rule has one
 * implementation (Z6 wires the parent flag into the gate).
 */
export function effectiveClipUrl(
  neuralVoiceEnabled: boolean,
  url: string | null | undefined,
): string | null {
  if (!neuralVoiceEnabled) return null;
  return typeof url === "string" && url.length > 0 ? url : null;
}

/**
 * Test seam. Drops the shared element and settles any in-flight clip
 * so a fresh test can stage a different ``window.Audio`` mock without
 * bleed-through. Production code never calls this.
 */
export function _resetClipAudioForTests(): void {
  const entry = activeClip;
  activeClip = null;
  if (entry !== null) {
    entry.finish(new Error(CLIP_INTERRUPTED_MESSAGE));
  }
  sharedAudio = null;
}
