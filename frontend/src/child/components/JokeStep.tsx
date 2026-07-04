// Phase K Step K12 — Kiosk joke step delivery.
//
// Renders a step of ``kind: "joke"`` as a two-beat setup/punchline. The
// setup appears immediately and is auto-spoken; after a 1.5s pause the
// punchline reveals and is auto-spoken too. Both lines are wrapped in
// ClickableText so word-level taps work, and the kiosk's K9
// ReadMeButton (mounted by StepCard, not here) re-plays BOTH lines on
// tap. K12 is kiosk-frontend-only — K13 wires the standalone-intent
// backend path that emits joke steps with ``setup`` (body) and
// ``punchline`` (metadata) populated; K14/K15 wire embedded + parent-
// insert + spontaneity sources of the same wire shape.
//
// Punchline source. The plan §K12 calls for the punchline to ride on
// ``step.metadata.punchline``. The backend ``build_interjection_step``
// helper (K14) is the producer; until it ships, this component reads
// defensively — when ``punchline`` is empty or missing, the second
// beat collapses (no reveal, no second TTS call) so a malformed wire
// payload renders as a setup-only step rather than crashing the kiosk.
// The re-play handler likewise gates on a non-empty punchline.
//
// Auto-play vs gesture unlock. The K8 TTS substrate's
// ``speak()`` no-ops outside a user-gesture stack on iOS Safari until
// the first in-gesture speak primes ``unlocked``. The kiosk's idle →
// approve → start sequence ALWAYS goes through the kid's "I'm Ready!"
// tap (Phase G) before any step renders, so by the time a joke step
// mounts the substrate is unlocked. We still call speak() — the
// substrate handles the no-op path silently.

import { useEffect, useRef, useState, type JSX } from "react";

import {
  effectiveClipUrl,
  isClipInterrupted,
  playClip,
  stopClip,
} from "../clip-audio";
import { cancel, speak, type VoiceProfile } from "../tts";
import { ClickableText } from "./ClickableText";

export interface JokeStepProps {
  // The setup line — comes from ``step.body`` after slot substitution.
  setup: string;
  // The punchline — comes from ``step.metadata.punchline``. May be
  // empty when the backend hasn't yet wired the field (K13/K14);
  // empty collapses the reveal beat and the re-play is setup-only.
  punchline: string;
  // Voice profile from the resolved persona (K8). Threaded by
  // StepCard so JokeStep doesn't need to reach into activity metadata.
  profile: VoiceProfile;
  // K9 click-to-read flag. When false, ClickableText renders the
  // setup/punchline as plain spans (no word taps); auto-play of the
  // full lines is unaffected.
  clickableWordsEnabled: boolean;
  // Phase Z Z5: server-rendered neural clips for the two beats
  // (``metadata.spoken_audio_setup_url`` / ``..._punchline_url``,
  // threaded by StepCard / RewardStep). Either may be absent — each
  // beat independently prefers its clip and falls back to Web Speech
  // on failure/absence (a 404 until the Z4 worker renders is DESIGNED
  // behavior). Absent both → the pre-Z5 Web Speech path, unchanged.
  setupClipUrl?: string | null;
  punchlineClipUrl?: string | null;
  // Phase Z Z5: neural-voice gate — defaults TRUE; Z6 wires the
  // ``neural_voice_enabled`` parent flag. Off → no clip attempts.
  neuralVoiceEnabled?: boolean;
}

// Reveal delay between setup and punchline. Plan §K12: "after a 1.5s
// pause". Mirrors a stand-up comic's beat — long enough for the kid
// to think about it, short enough that they don't lose interest.
const PUNCHLINE_REVEAL_MS = 1500;

// Phase Z Z5: watchdog bound for the clip path's setup beat. tts.ts
// documents that iOS can silently drop a cancel-adjacent utterance
// with NO events (speak()'s promise never settles), and a wedged media
// element could likewise never fire ended/error — either would leave
// the chained punchline waiting forever. Pre-Z5 was immune (the
// punchline was timer-fired and engine-queued), so the clip path must
// not regress "the punchline beat eventually happens": after this
// bound the sequence proceeds as if the setup finished. 12s comfortably
// exceeds any real setup line (~150 chars ≈ 10s at Kokoro pace); the
// worst misfire is a punchline starting 12s in — strictly better than
// never. Exported for the fake-timer tests.
export const SETUP_AUDIO_WATCHDOG_MS = 12_000;

// Race ``promise`` against the watchdog. RESOLVES on timeout (the
// degrade is "fire the punchline beat anyway"); passes a rejection
// through untouched (an interruption must still abort the sequence).
// The timer is cleared as soon as the real promise settles so tests
// and long-lived kiosk sessions don't accumulate dangling timeouts.
function withSetupWatchdog(promise: Promise<void>): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const id = setTimeout(resolve, SETUP_AUDIO_WATCHDOG_MS);
    promise.then(
      () => {
        clearTimeout(id);
        resolve();
      },
      (err: unknown) => {
        clearTimeout(id);
        reject(err instanceof Error ? err : new Error(String(err)));
      },
    );
  });
}

// Phase Z Z5: playback-generation token, shared by the component's
// autoplay and ``replayJoke``. Every new playback sequence bumps it; a
// pending punchline chain compares its captured value before starting
// audio and goes silent when superseded. Why interruption alone isn't
// enough: once the setup beat's promise has SETTLED (a short setup clip
// that ends inside the 1.5s reveal gap), the autoplay chain holds no
// in-flight clip a replay could interrupt — without the token, the
// reveal timer would fire the autoplay punchline ON TOP of a replay
// started in that gap. Module scope (not a ref) because replayJoke is
// a plain function and only one joke surface plays at a time on the
// kiosk (StepCard mounts one step; a replay tap and the autoplay share
// this counter by design).
let playbackGeneration = 0;

export function JokeStep(props: JokeStepProps): JSX.Element {
  const {
    setup,
    punchline,
    profile,
    clickableWordsEnabled,
    setupClipUrl = null,
    punchlineClipUrl = null,
    neuralVoiceEnabled = true,
  } = props;
  // ``revealed`` flips to true after the setTimeout fires; the
  // punchline DOM (and its TTS call) is gated on this state so a
  // re-render mid-pause doesn't double-fire the speak() pipeline.
  const [revealed, setRevealed] = useState(false);
  // Track whether we've already fired the auto-play sequence so a
  // re-render (StrictMode double-effect, prop change to profile, etc.)
  // doesn't trigger a second auto-play. The ReadMeButton's re-play
  // path bypasses this guard intentionally — a tap is a user request,
  // not an auto-play.
  const autoplayedRef = useRef(false);

  useEffect(() => {
    // Guard the double-effect: StrictMode mounts twice in dev. We don't
    // want two concurrent setup speaks racing each other on the same
    // mount lifecycle.
    if (autoplayedRef.current) return;
    autoplayedRef.current = true;
    // Phase Z Z5: effective clip URLs (null when the neural-voice gate
    // is off or the wire didn't carry one). When NEITHER beat has a
    // clip the whole effect stays on the pre-Z5 Web Speech path,
    // byte-identical, including the queue-at-reveal punchline call.
    const sUrl = effectiveClipUrl(neuralVoiceEnabled, setupClipUrl);
    const pUrl = effectiveClipUrl(neuralVoiceEnabled, punchlineClipUrl);
    const clipPath = sUrl !== null || pUrl !== null;
    // Unmount latch for the async punchline chain below — the reveal
    // timer is clearable, but a promise continuation is not, so the
    // chain checks this before starting punchline audio.
    let disposed = false;
    // This autoplay is a new playback sequence: bump the generation and
    // capture it for the chain's staleness check (a replay tap bumps it
    // again, silencing this chain — see the token's doc above).
    playbackGeneration += 1;
    const generation = playbackGeneration;

    // "Setup audio finished" promise (clip path only). Resolves when
    // the setup beat's audio ends — clip ended, or the fallback
    // utterance completed, or the 12s watchdog fired on a hung leg;
    // rejects on interruption (another surface took audio focus — the
    // punchline must NOT fire then) or an unrecoverable speech error.
    // Why the chain exists: Web Speech QUEUES utterances natively (the
    // pre-Z5 punchline call at the reveal simply waits its turn in the
    // engine), but the shared clip element cannot queue — starting the
    // punchline clip at the 1.5s reveal would truncate a still-playing
    // setup clip mid-sentence. Chaining punchline audio on this promise
    // reproduces the engine's queueing semantics on the clip path.
    let setupAudioDone: Promise<void> | null = null;
    if (!clipPath) {
      // Pre-Z5 path. Setup speaks immediately. Errors are swallowed —
      // the kid still SEES the text, so a missing engine isn't a
      // kiosk-blocker.
      void speak(setup, profile).catch(() => {});
    } else if (sUrl !== null) {
      setupAudioDone = withSetupWatchdog(
        playClip(sUrl).catch((err: unknown) => {
          // Interruption aborts the sequence (focus moved on purpose);
          // any other failure (404-until-rendered, decode, autoplay-
          // block) falls back to Web Speech for the setup — the sequence
          // then continues from the punchline (never restarts the setup).
          if (isClipInterrupted(err)) throw err;
          return speak(setup, profile);
        }),
      );
    } else {
      // Mixed wire shape: no setup clip but the punchline has one.
      // Speak the setup (stopClip keeps single audio focus) and gate
      // the punchline clip on the utterance's END — playClip cancels
      // in-flight speech, so firing it early would cut the setup off.
      stopClip();
      setupAudioDone = withSetupWatchdog(speak(setup, profile));
    }
    if (setupAudioDone !== null) {
      // Pre-attach a swallow handler so an early rejection (e.g. an
      // interrupt before the reveal timer fires) never surfaces as an
      // unhandled rejection. The reveal chain below attaches its own
      // handlers to the SAME promise — this extra branch doesn't
      // consume the rejection for it.
      void setupAudioDone.catch(() => {});
    }
    // Schedule the reveal + punchline audio. The 1.5s VISUAL beat is
    // identical on both paths — only the punchline's audio start
    // differs (engine queue vs. explicit chain, see above).
    const setupDone = setupAudioDone;
    const id = setTimeout(() => {
      setRevealed(true);
      if (punchline.length > 0) {
        if (setupDone === null) {
          // Pre-Z5 path. We deliberately do NOT cancel the setup — a
          // fast reader on a slow engine might still be mid-utterance,
          // and the kid's experience is "setup, beat, punchline" not
          // "setup gets truncated when the punchline arrives." Modern
          // Web Speech queues utterances naturally; if the setup is
          // still speaking the punchline plays right after.
          void speak(punchline, profile).catch(() => {});
        } else {
          void setupDone
            .then(() => {
              // Staleness gates, checked AFTER the setup beat settles:
              // ``disposed`` covers unmount; the generation token covers
              // the settled-setup race — a replay started in the reveal
              // gap (short setup clip already ended) left nothing for
              // playClip's interrupt to reject, so without this check
              // the autoplay punchline would fire over the replay.
              if (disposed || generation !== playbackGeneration) return;
              if (pUrl !== null) {
                return playClip(pUrl).catch((err: unknown) => {
                  // Mid-sequence fallback: the REMAINDER (punchline
                  // only) drops to Web Speech — never re-speak the
                  // setup the kid already heard.
                  if (isClipInterrupted(err)) throw err;
                  return speak(punchline, profile);
                });
              }
              // Setup rode a clip but the punchline has none: take clip
              // focus before speaking (a clip that slipped in during
              // the beat must not play under the utterance).
              stopClip();
              return speak(punchline, profile);
            })
            .catch(() => {
              // Interrupted (replay/another surface owns audio now) or
              // a speech-engine error — either way the sequence ends
              // silently; the punchline TEXT is already revealed.
            });
        }
      }
    }, PUNCHLINE_REVEAL_MS);
    return () => {
      // Effect cleanup runs on unmount AND on dep-change. We only have
      // [] deps so this fires on unmount. Clearing the timer prevents
      // a setRevealed call on an unmounted component (React warning).
      disposed = true;
      clearTimeout(id);
      // ``cancel()`` is itself idempotent and silent — if no speech is
      // in flight it's a no-op. Calling it on unmount makes sure a
      // mid-utterance kiosk that advances doesn't leak audio into the
      // next step's render. Phase Z Z5: ``stopClip()`` is the clip-side
      // mirror — a mid-clip advance must not leak the old step's audio
      // (it also rejects the pending sequence as interrupted, which the
      // handlers above swallow by design).
      cancel();
      stopClip();
    };
    // setup / punchline / profile / clip URLs are stable for a given
    // mounted step (StepCard keys on step.seq, so a new step is a new
    // mount). We intentionally fire the effect once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      data-testid="joke-step"
      data-revealed={revealed ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 24,
        textAlign: "center",
        maxWidth: 900,
      }}
    >
      <div
        data-testid="joke-setup"
        style={{
          fontSize: "clamp(2rem, 5vw, 4rem)",
          lineHeight: 1.2,
          fontWeight: 700,
          color: "#222",
        }}
      >
        <ClickableText
          text={setup}
          profile={profile}
          enabled={clickableWordsEnabled}
        />
      </div>
      {/*
        Punchline reveal. We gate on ``revealed`` AND a non-empty
        punchline — the latter so a backend that hasn't yet wired the
        field (K13/K14) renders a setup-only joke rather than an empty
        bubble. Once K14's ``build_interjection_step`` ships the field
        consistently, this defensive empty-check becomes dead-but-
        harmless code that a future cleanup pass can remove.
      */}
      {revealed && punchline.length > 0 && (
        <div
          data-testid="joke-punchline"
          style={{
            fontSize: "clamp(1.5rem, 4vw, 3rem)",
            lineHeight: 1.2,
            fontWeight: 600,
            color: "#1976d2",
            // Slight indent on the punchline reads as a "second beat"
            // visually — the kid sees the structure even if they can't
            // read fluently yet.
            marginTop: 8,
          }}
        >
          <ClickableText
            text={punchline}
            profile={profile}
            enabled={clickableWordsEnabled}
          />
        </div>
      )}
    </div>
  );
}

/**
 * Phase Z Z5: optional clip URLs for ``replayJoke``. Carried as an
 * options object so pre-Z5 call sites (and tests) keep their 3-arg
 * shape — absent options replay via Web Speech exactly as before.
 */
export interface ReplayJokeClips {
  setupClipUrl?: string | null;
  punchlineClipUrl?: string | null;
  // Defaults TRUE (Z6 wires the parent flag); false → no clip attempts.
  neuralVoiceEnabled?: boolean;
}

/**
 * Re-play both lines (setup + punchline if present). Exposed as a
 * standalone helper because the K9 ReadMeButton's contract is
 * "speak this text" and the joke step has TWO lines, not one. The
 * StepCard mounts a customized ReadMeButton-equivalent click handler
 * that calls this helper when the kid taps the watermark on a joke
 * step. Living outside the component because it has no React state —
 * a pure side-effect on the TTS/clip substrates.
 *
 * Phase Z Z5: when clips are available (and the gate is on) the replay
 * plays them SEQUENTIALLY — setup clip, then punchline clip when the
 * setup's audio ends — because the shared clip element cannot queue
 * the way Web Speech does. Each beat falls back to Web Speech on
 * failure (mid-sequence fallback covers the REMAINDER only; the setup
 * is never restarted). An interruption (the kid tapped replay again,
 * or another surface took focus) aborts the remainder silently.
 */
export function replayJoke(
  setup: string,
  punchline: string,
  profile: VoiceProfile,
  clips: ReplayJokeClips = {},
): void {
  const flagOn = clips.neuralVoiceEnabled !== false;
  const sUrl = effectiveClipUrl(flagOn, clips.setupClipUrl);
  const pUrl = effectiveClipUrl(flagOn, clips.punchlineClipUrl);
  // Every replay is a new playback sequence: bump the generation so any
  // pending punchline chain (the autoplay's, or an earlier replay's)
  // goes stale even when its setup beat has ALREADY settled — the case
  // playClip's interrupt cannot reach (see the token's doc above).
  playbackGeneration += 1;
  const generation = playbackGeneration;
  if (sUrl === null && pUrl === null) {
    // Pre-Z5 Web Speech replay, unchanged (plus the Z5 single-audio-
    // focus stopClip — a no-op unless a clip is somehow mid-play).
    stopClip();
    // Interrupt anything in flight (the user just tapped Read Me — they
    // want a fresh start, not a queue).
    cancel();
    // Speak both back-to-back. Web Speech queues, so they play in order.
    void speak(setup, profile).catch(() => {});
    if (punchline.length > 0) {
      void speak(punchline, profile).catch(() => {});
    }
    return;
  }
  // Clip-bearing replay. ``playClip`` takes audio focus itself
  // (cancels speech + interrupts the previous clip — which is exactly
  // how a rapid double-tap restarts from the setup: the second tap's
  // playClip rejects the first tap's pending chain as interrupted).
  let setupDone: Promise<void>;
  if (sUrl !== null) {
    setupDone = withSetupWatchdog(
      playClip(sUrl).catch((err: unknown) => {
        if (isClipInterrupted(err)) throw err;
        return speak(setup, profile);
      }),
    );
  } else {
    // Mixed shape: speech setup, clip punchline. Chain on the
    // utterance's END so the punchline clip (whose playClip cancels
    // in-flight speech) doesn't cut the setup off. A rejected setup
    // utterance (canceled by a newer replay, or engine error) aborts
    // the remainder via the chain's final catch — proceeding would
    // steal focus from whatever canceled us.
    stopClip();
    cancel();
    setupDone = withSetupWatchdog(speak(setup, profile));
  }
  if (punchline.length === 0) {
    void setupDone.catch(() => {});
    return;
  }
  void setupDone
    .then(() => {
      // Superseded by a newer replay/autoplay while the setup beat was
      // in flight OR after it settled — either way the newer sequence
      // owns audio focus now.
      if (generation !== playbackGeneration) return;
      if (pUrl !== null) {
        return playClip(pUrl).catch((err: unknown) => {
          // Mid-sequence fallback for the REMAINDER (punchline only).
          if (isClipInterrupted(err)) throw err;
          return speak(punchline, profile);
        });
      }
      // Clip setup + speech punchline: take clip focus first (mirrors
      // the component chain).
      stopClip();
      return speak(punchline, profile);
    })
    .catch(() => {
      // Interrupted or unrecoverable — end the replay silently.
    });
}
