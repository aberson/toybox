// Phase K Step K12 — Kiosk song player.
//
// Renders a step of ``kind: "song"`` as an ``<audio>`` element with a
// visible title + state indicator and a Next button that enables on
// ``onended``. K12 is kiosk-frontend-only — the backend wiring that
// emits these steps lands in K13 (standalone surface), K14 (embedded +
// endings) and K15 (parent-insert + spontaneity). Until then this
// component is exercised only via vitest fixtures.
//
// Audio URL construction. K12 doesn't ship a backend ``/api/songs``
// route — the plan §"Do NOT touch backend in K12" is explicit. The
// component reads ``src`` directly from its props; ``StepCard`` is the
// integration seam that derives ``src`` from ``step.metadata``. The
// derivation lives in StepCard rather than here so the component stays
// pure (easier to vitest, easier to swap when K13 finalizes the wire
// shape). The two metadata sources supported today:
//
//   1. ``step.metadata.audio_url`` — absolute or backend-relative URL
//      the backend has already constructed. Preferred path once K14
//      ships ``build_interjection_step`` because that builder will
//      already have computed the URL.
//   2. ``step.metadata.song_id`` — corpus id; the kiosk falls back to
//      ``/api/static/songs/audio/<id>.mp3`` (mirrors the existing
//      ``/api/static/images`` mount pattern). The mount itself will be
//      added in K13 alongside the standalone-intent backend wire.
//
// State machine:
//
//   idle      — mounted, autoplay not yet attempted.
//   playing   — autoplay (or operator tap on the play button) succeeded
//                AND ``onended`` hasn't fired.
//   paused    — visible play/pause button was tapped while ``playing``.
//   blocked   — autoplay rejected (engine policy / no gesture); a
//                manual play button is shown to the kid.
//   error     — the <audio> element fired ``onerror`` (404, decoder
//                failure, etc.). After a 2s grace the Next button
//                enables so the kiosk doesn't deadlock on a bad asset.
//   done      — ``onended`` fired; Next button is enabled.
//
// We deliberately do NOT mount a ReadMeButton — the song IS the audio
// surface, and a competing TTS read-aloud would interrupt the song.
// StepCard's READ_ME_ELIGIBLE_KINDS already excludes ``song``.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

export interface SongPlayerProps {
  src: string;
  title: string;
  onEnded?: () => void;
}

type SongState = "idle" | "playing" | "paused" | "blocked" | "error" | "done";

// Grace period after an <audio> error before we surface a "Skip" / Next
// button. Plan §K12 specifies 2s — long enough that a transient decoder
// hiccup might still recover (Safari occasionally fires error then
// auto-recovers on its own), short enough that a kid isn't stuck
// staring at a song that won't play.
const ERROR_SKIP_GRACE_MS = 2000;

export function SongPlayer(props: SongPlayerProps): JSX.Element {
  const { src, title, onEnded } = props;
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<SongState>("idle");
  // Tracks whether the post-error grace timer has elapsed. We render
  // the "Skip" affordance only after the grace so a quick recovery
  // doesn't flash the skip button at the kid.
  const [errorSkipReady, setErrorSkipReady] = useState(false);

  // Attempt autoplay on mount. The first call inside a user gesture
  // unlocks the audio engine (same pattern as the K8 TTS substrate).
  // If autoplay is rejected (iOS / Chrome autoplay policy), we fall to
  // ``blocked`` and surface a manual play button.
  useEffect(() => {
    const el = audioRef.current;
    if (el === null) return;
    // Use ``play()``'s promise to detect the autoplay-policy rejection
    // path. Some legacy engines return undefined; treat that as
    // "started" (older Safari) — the ``onplay`` handler will catch up.
    const maybe = el.play();
    if (maybe !== undefined && typeof maybe.then === "function") {
      maybe.catch(() => {
        // Rejected — most commonly the autoplay policy. Flip to blocked
        // so the manual play button renders. Never throw to the kid.
        setState((prev) => (prev === "idle" ? "blocked" : prev));
      });
    }
    // No cleanup needed: the audio element unmounts with the component,
    // which automatically pauses and releases the resource.
    // ``src`` is intentionally NOT in the dep array — a src change
    // would unmount and remount the SongPlayer (StepCard keys it off
    // step.seq), so a single-mount autoplay is the correct contract.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Error grace timer. Run once when we enter the error state. The
  // timer ID is captured in a ref so a fast unmount cancels cleanly.
  useEffect(() => {
    if (state !== "error") {
      setErrorSkipReady(false);
      return;
    }
    const id = setTimeout(() => {
      setErrorSkipReady(true);
    }, ERROR_SKIP_GRACE_MS);
    return () => {
      clearTimeout(id);
    };
  }, [state]);

  const handlePlayClick = useCallback((): void => {
    const el = audioRef.current;
    if (el === null) return;
    if (state === "playing") {
      el.pause();
      setState("paused");
      return;
    }
    const maybe = el.play();
    if (maybe !== undefined && typeof maybe.then === "function") {
      maybe.catch(() => {
        setState("blocked");
      });
    }
  }, [state]);

  const handlePlay = useCallback((): void => {
    setState((prev) => (prev === "done" ? prev : "playing"));
  }, []);

  const handlePause = useCallback((): void => {
    // Don't override ``done`` — the engine fires ``pause`` after
    // ``ended`` on some browsers and we want the Next button to stay
    // enabled. Also keep ``error`` sticky so the grace timer rules.
    setState((prev) =>
      prev === "playing" ? "paused" : prev === "idle" ? "blocked" : prev,
    );
  }, []);

  const handleEnded = useCallback((): void => {
    setState("done");
    // Defensive: ``onEnded`` is optional so callers that only care
    // about visual playback can omit it.
    if (onEnded !== undefined) {
      try {
        onEnded();
      } catch {
        // The kiosk shouldn't crash if the parent's callback throws;
        // the visible Next button will still let the kid advance.
      }
    }
  }, [onEnded]);

  const handleError = useCallback((): void => {
    // 404, decoder failure, malformed mp3, etc. We enter ``error`` and
    // the grace-timer effect will flip ``errorSkipReady`` after 2s.
    setState("error");
  }, []);

  const stateLabel = stateToLabel(state);
  // Next button is enabled on ``done`` (the happy path) AND after the
  // error grace timer fires (so the kiosk doesn't deadlock on a 404).
  const nextEnabled = state === "done" || (state === "error" && errorSkipReady);

  return (
    <div
      data-testid="song-player"
      data-state={state}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 16,
        padding: 24,
        maxWidth: 600,
        width: "100%",
      }}
    >
      <h2
        data-testid="song-player-title"
        style={{
          margin: 0,
          fontSize: "clamp(1.5rem, 4vw, 3rem)",
          fontWeight: 700,
          color: "#222",
          textAlign: "center",
        }}
      >
        {title}
      </h2>
      <div
        data-testid="song-player-state-label"
        aria-live="polite"
        style={{
          fontSize: 18,
          color: "#555",
          letterSpacing: 0.5,
        }}
      >
        {stateLabel}
      </div>
      {/*
        The <audio> element is the playback surface. ``controls`` is
        intentionally OFF — the kiosk owns the play/pause via the big
        button below so the kid can't scrub or change volume. ``preload
        ="auto"`` lets the engine fetch + buffer eagerly so autoplay's
        first-frame latency is hidden by the (very short) mount-time
        animation. We give it an ``aria-label`` so screen readers get
        context even though it's not visually rendered.
      */}
      <audio
        ref={audioRef}
        data-testid="song-player-audio"
        src={src}
        preload="auto"
        aria-label={`Song: ${title}`}
        onPlay={handlePlay}
        onPause={handlePause}
        onEnded={handleEnded}
        onError={handleError}
      />
      {/*
        Manual play/pause control. Visible always (so the kid can pause
        a song mid-play) but the LABEL changes by state — "Play" when
        idle / blocked / paused / done, "Pause" while playing. The
        ``blocked`` state surfaces the SAME button so the kid's
        physical action (tap) provides the gesture the engine needs to
        unlock playback.
      */}
      <button
        type="button"
        data-testid="song-player-toggle"
        onClick={handlePlayClick}
        // Disable the play/pause toggle in the done state so a
        // confused tap doesn't restart the song after it ended.
        disabled={state === "done" || state === "error"}
        style={TOGGLE_BUTTON_STYLE}
      >
        {state === "playing" ? "Pause" : "Play"}
      </button>
      <button
        type="button"
        data-testid="song-player-next"
        onClick={() => {
          // Manual Next: same callback path as onEnded. We don't
          // change ``state`` because the parent will unmount us as
          // it advances to the next step.
          if (onEnded !== undefined) {
            try {
              onEnded();
            } catch {
              // See handleEnded — never crash the kiosk on a parent
              // callback error.
            }
          }
        }}
        disabled={!nextEnabled}
        style={{
          ...NEXT_BUTTON_STYLE,
          opacity: nextEnabled ? 1 : 0.4,
          cursor: nextEnabled ? "pointer" : "not-allowed",
        }}
      >
        Next
      </button>
    </div>
  );
}

function stateToLabel(state: SongState): string {
  switch (state) {
    case "idle":
      return "Loading...";
    case "playing":
      return "Playing";
    case "paused":
      return "Paused";
    case "blocked":
      return "Tap Play to start";
    case "error":
      return "Could not play this song";
    case "done":
      return "Finished";
  }
}

const TOGGLE_BUTTON_STYLE = {
  padding: "16px 48px",
  fontSize: 24,
  fontWeight: 600,
  borderRadius: 12,
  border: "2px solid #1976d2",
  background: "white",
  color: "#1976d2",
  cursor: "pointer",
  minHeight: 56,
} as const;

const NEXT_BUTTON_STYLE = {
  padding: "16px 56px",
  fontSize: 24,
  fontWeight: 700,
  borderRadius: 12,
  border: "none",
  background: "#1976d2",
  color: "white",
  minHeight: 56,
} as const;
