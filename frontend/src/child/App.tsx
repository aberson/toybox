import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, JSX } from "react";

import {
  ApiClient,
  ApiError,
  isAbortError,
  retryWithBackoff,
  withConflictHandler,
} from "./api";
import type { Activity } from "./api";
import type { ChoiceResult } from "./components/ChoiceButton";
import { KioskPinPrompt } from "./components/KioskPinPrompt";
import { PersonaAvatar } from "./components/PersonaAvatar";
import { StepCard } from "./components/StepCard";
import { playSfx, preloadSfx } from "./sfx";
import {
  currentStepSeq,
  isTerminalState,
  shouldFireSuccessSfx,
  shouldFireTransitionSfx,
  useChildStore,
} from "./store";
import {
  acquireWakeLock,
  releaseWakeLock,
  watchVisibilityForReacquire,
  type WakeLockSentinel,
} from "./wakeLock";
import { ChildWsClient } from "./ws";
import type { Envelope } from "./ws";

// Mirrors parent App's deriveWsUrl. The token rides in-band as the
// first ws message, never in the URL.
function deriveWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

// Dev-shim PIN sources for the kiosk. The kiosk does not own the parent
// PIN — production replaces this entire path with the kiosk pairing
// flow (Phase D Step 20). Until then, the bootstrap accepts:
//   1. ``window.__TOYBOX_KIOSK_PIN__`` — set by the test harness pre-mount.
//      Wins because existing tests depend on this precedence.
//   2. ``localStorage["toybox.kiosk.pin"]`` — survives reload, so a
//      developer can set it once via DevTools and the kiosk authenticates
//      thereafter. Plaintext storage is acceptable for this dev shim.
// Values must match the backend's 4-12 digit PIN format; bad shapes
// fall through to the "not configured" toast rather than 422 the backend.
const KIOSK_PIN_STORAGE_KEY = "toybox.kiosk.pin";

function isValidKioskPin(s: unknown): s is string {
  return typeof s === "string" && /^[0-9]{4,12}$/.test(s);
}

function readKioskBootstrapPin(): string {
  const w = window as unknown as { __TOYBOX_KIOSK_PIN__?: string };
  if (isValidKioskPin(w.__TOYBOX_KIOSK_PIN__)) {
    return w.__TOYBOX_KIOSK_PIN__;
  }
  try {
    const stored = window.localStorage.getItem(KIOSK_PIN_STORAGE_KEY);
    if (isValidKioskPin(stored)) {
      return stored;
    }
  } catch {
    // localStorage can throw on disabled storage / strict sandboxing.
    // Fall through to "" so the bootstrap shows the PIN prompt rather
    // than crashing the kiosk shell.
  }
  return "";
}

function storeKioskPin(pin: string): void {
  try {
    window.localStorage.setItem(KIOSK_PIN_STORAGE_KEY, pin);
  } catch {
    // Storage disabled — the prompt's onSubmit caller will still fall
    // through to the bootstrap and either succeed (window var set) or
    // re-prompt. Nothing else we can do.
  }
}

function clearStoredKioskPin(): void {
  try {
    window.localStorage.removeItem(KIOSK_PIN_STORAGE_KEY);
  } catch {
    // See storeKioskPin — pass.
  }
}

// Outer layer: pinned to the viewport edges so the gradient bleeds into
// the iPad's rounded corners with no color band. Carries no padding —
// the inner content layer handles safe-area inset spacing.
const FULL_BLEED_BACKGROUND_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  margin: 0,
  background: "linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)",
  boxSizing: "border-box",
  overflow: "hidden",
};

// Inner layer: holds all kiosk content, centered. Padding uses
// env(safe-area-inset-*) so on iPad the content clears the camera notch
// / home indicator while the gradient stays edge-to-edge. The 32px
// fallback matches prior desktop rendering (env() returns 0 outside
// iPad-style safe areas, so the fallback is what actually applies).
const FULL_BLEED_CONTENT_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 32,
  fontFamily: "system-ui, sans-serif",
  width: "100%",
  height: "100%",
  boxSizing: "border-box",
  padding:
    "env(safe-area-inset-top, 32px) env(safe-area-inset-right, 32px) env(safe-area-inset-bottom, 32px) env(safe-area-inset-left, 32px)",
};

// Single source of truth for "the kiosk is currently displaying an
// active, non-terminal activity that should keep the screen awake."
// Extracted so the wake-lock effect (`wantLock`) and the render path
// (`showActive`) cannot drift if a future state is added — both
// derive from this helper.
function isActiveKioskActivity(activity: Activity | null): boolean {
  if (activity === null) return false;
  if (isTerminalState(activity.state)) return false;
  return activity.state === "approved" || activity.state === "running";
}

function avatarLetter(activity: Activity | null): string {
  if (activity === null) return "?";
  // Prefer the persona's display_name first char (e.g. "M" for
  // Marvelous, "P" for Princess Lyra) so the avatar varies as personas
  // do. Falls back to persona_id, then title, then "?".
  const meta = activity.metadata as Record<string, unknown>;
  const persona = meta["persona"];
  if (typeof persona === "object" && persona !== null) {
    const dn = (persona as Record<string, unknown>)["display_name"];
    if (typeof dn === "string" && dn.length > 0) return dn[0]!;
  }
  if (activity.persona_id !== null && activity.persona_id.length > 0) {
    return activity.persona_id[0]!;
  }
  if (activity.title !== null && activity.title.length > 0) {
    return activity.title[0]!;
  }
  return "?";
}

function avatarImage(activity: Activity | null): string | null {
  // The Activity wire shape doesn't include a persona object yet
  // (M5 will land the library + avatar paths). When it does, we look
  // it up under metadata.persona.avatar_image_path; otherwise fall
  // back to the colored letter circle.
  if (activity === null) return null;
  const meta = activity.metadata as Record<string, unknown>;
  const persona = meta["persona"];
  if (typeof persona === "object" && persona !== null) {
    const path = (persona as Record<string, unknown>)["avatar_image_path"];
    if (typeof path === "string" && path.length > 0) return path;
  }
  return null;
}

export function App(): JSX.Element {
  const state = useChildStore();
  const apiRef = useRef<ApiClient | null>(null);
  const wsRef = useRef<ChildWsClient | null>(null);
  // Sentinel `-1` means "no step ever seen". The first envelope where
  // `currentStepSeq >= 0` will set this without firing the SFX (the
  // `prev >= 0` check below). Otherwise initial 0 → 1 would always
  // fire `transition.wav` even though no real step transition occurred.
  const prevStepSeqRef = useRef<number>(-1);
  // Track whether we've seen ANY terminal flag yet — protects against
  // the kiosk attaching to an already-completed activity playing
  // `success.wav` for a stale terminal state.
  const prevTerminalSeenRef = useRef<boolean>(false);
  const prevTerminalRef = useRef<boolean>(false);
  const [busyAdvance, setBusyAdvance] = useState(false);
  // Phase G G4: which choice button (if any) currently has a POST in
  // flight. The ``choosingIndex`` STATE drives the post-commit visual
  // gate (drilled through StepCard to each ChoiceButton's ``disabled``
  // prop, so siblings render disabled while one is in flight). The
  // ``choosingRef`` is the SYNCHRONOUS gate — handleChoose checks +
  // sets the ref before any await, so a second tap on a different
  // button within the same tick (before React commits the disabled
  // re-render) still bails on the ref check. Belt-and-braces: the
  // ChoiceButton component holds its own internal ref guard too, but
  // the App-level ref handles the cross-button case where two SIBLING
  // buttons each read their own busy-state as false in the same frame.
  const [choosingIndex, setChoosingIndex] = useState<number | null>(null);
  const choosingRef = useRef<number | null>(null);
  // Step 21 dev-shim: when no valid PIN is configured (or the cached
  // value is rejected by the backend), render KioskPinPrompt instead of
  // toasting. ``bootCounter`` re-runs the bootstrap effect after the
  // user submits a new PIN — incrementing it triggers cleanup → setup
  // with the new localStorage value picked up by readKioskBootstrapPin.
  const [pinPromptVisible, setPinPromptVisible] = useState(false);
  const [pinPromptError, setPinPromptError] = useState<string | null>(null);
  const [bootCounter, setBootCounter] = useState(0);

  if (apiRef.current === null) {
    apiRef.current = new ApiClient({
      getToken: () => useChildStore.getState().token,
    });
  }
  const api = apiRef.current;

  // Phase A bootstrap: same shape as the parent — issue a parent token
  // (kiosk pairing arrives in Phase D Step 20), open ws, wait for an
  // approved/running activity to arrive on the wire.
  useEffect(() => {
    const aborter = new AbortController();
    let mounted = true;
    // Preload sfx in the background. If the file is missing the
    // asset is silently marked dead — see sfx.ts.
    preloadSfx("transition");
    preloadSfx("success");
    const boot = async (): Promise<void> => {
      try {
        // Step 21: ``/api/auth/parent`` is now PIN-gated. PIN sources
        // are described above on ``readKioskBootstrapPin``. When no
        // valid PIN is configured we render KioskPinPrompt instead of
        // POSTing an empty body (would 422). 401 from the backend is
        // treated as "stale cached PIN" — clear and re-prompt. 423
        // (locked) and other errors still toast — they're not user-
        // recoverable from the kiosk's PIN form.
        const bootstrapPin = readKioskBootstrapPin();
        if (bootstrapPin === "") {
          if (mounted) {
            setPinPromptError(null);
            setPinPromptVisible(true);
          }
          return;
        }
        const tokenResp = await retryWithBackoff(() =>
          api.issueParentToken({ pin: bootstrapPin }, { signal: aborter.signal }),
        );
        if (!mounted) return;
        setPinPromptVisible(false);
        setPinPromptError(null);
        useChildStore.getState().setToken(tokenResp);
        const ws = new ChildWsClient({
          url: deriveWsUrl(),
          getToken: () => useChildStore.getState().token,
          onEnvelope: (env: Envelope) => {
            useChildStore.getState().applyEnvelope(env);
          },
          onState: (s) => {
            useChildStore.getState().setWsState(s);
          },
          onRejected: (rejected) => {
            useChildStore.getState().applyRejectedTopics(rejected);
          },
          onReconnect: () => {
            const cur = useChildStore.getState().activity;
            if (cur === null) return;
            void api
              .getActivity(cur.id, { signal: aborter.signal })
              .then((fresh) => {
                if (!mounted) return;
                // applyReconnectResync drops the GET if a newer
                // envelope already won (see store.ts comment). Avoids
                // the Step 9 race where an in-flight refetch could
                // clobber a fresher in-memory activity.
                useChildStore.getState().applyReconnectResync(fresh);
              })
              .catch((err: unknown) => {
                if (isAbortError(err)) return;
                // Surface non-abort errors so the parent can spot a
                // stuck reconnect refetch instead of silently dropping
                // it. The ws stream itself is the source of truth, so
                // this is a warning, not an error.
                const message =
                  err instanceof Error ? err.message : "reconnect refetch failed";
                useChildStore
                  .getState()
                  .pushToast("warning", `reconnect: ${message}`);
              });
          },
        });
        // StrictMode double-mount (and any unmount mid-await) can have
        // already run cleanup before we land here. If so, abandon this
        // ws — wsRef is null'd and the AbortController is fired, but
        // those happen on the OLD effect run, not this boot()'s
        // closure. Without this guard we'd leak a live ws client.
        if (!mounted) {
          ws.stop();
          return;
        }
        wsRef.current = ws;
        ws.start();
      } catch (err) {
        if (!mounted || isAbortError(err)) return;
        if (err instanceof ApiError && err.status === 401) {
          // The cached PIN was rejected (e.g. parent rotated their PIN).
          // Drop the bad value and re-prompt so the user can supply the
          // current one without resorting to DevTools.
          clearStoredKioskPin();
          setPinPromptError("Wrong PIN — try again.");
          setPinPromptVisible(true);
          return;
        }
        const message =
          err instanceof Error ? err.message : "bootstrap failed";
        useChildStore.getState().pushToast("error", `bootstrap: ${message}`);
      }
    };
    void boot();
    return () => {
      mounted = false;
      aborter.abort();
      wsRef.current?.stop();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootCounter]);

  const handlePinPromptSubmit = useCallback((pin: string): void => {
    storeKioskPin(pin);
    setPinPromptError(null);
    setPinPromptVisible(false);
    setBootCounter((c) => c + 1);
  }, []);

  const refetchActivity = useCallback(
    async (id: string): Promise<Activity | null> => {
      try {
        return await api.getActivity(id);
      } catch {
        return null;
      }
    },
    [api],
  );

  const activity = state.activity;

  // SFX on transitions. ``prevStepSeqRef`` starts at -1 (sentinel:
  // "no step ever seen") so the first envelope records the seq without
  // firing — see ``shouldFireTransitionSfx`` for the pure decision.
  useEffect(() => {
    const seq = currentStepSeq(activity);
    if (shouldFireTransitionSfx(prevStepSeqRef.current, seq)) {
      playSfx("transition");
    }
    prevStepSeqRef.current = seq;
  }, [activity]);

  // Success SFX on the first time we observe a transition INTO a
  // completed/ended state. ``prevTerminalSeenRef`` guards against
  // attaching to an already-terminal activity (e.g. on a page reload
  // mid-completion) playing ``success.wav`` for a stale terminal
  // state — see ``shouldFireSuccessSfx``.
  useEffect(() => {
    const completedOrEnded =
      activity !== null &&
      isTerminalState(activity.state) &&
      (activity.state === "completed" || activity.state === "ended");
    if (
      shouldFireSuccessSfx({
        prevTerminal: prevTerminalRef.current,
        nextTerminal: completedOrEnded,
        hasSeenAny: prevTerminalSeenRef.current,
      })
    ) {
      playSfx("success");
    }
    if (activity !== null) {
      prevTerminalSeenRef.current = true;
    }
    prevTerminalRef.current = completedOrEnded;
  }, [activity]);

  // Screen Wake Lock — keep the iPad display awake while an activity
  // is approved/running. Released on terminal states or when the
  // activity disappears. Browsers without the Wake Lock API silently
  // no-op (acquireWakeLock returns null). The visibility watcher
  // re-acquires after the OS releases the lock on backgrounding.
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);
  // In-flight guard for acquireWakeLock. Two concurrent triggers (the
  // activity-change effect AND the visibilitychange handler) can both
  // observe `wakeLockRef.current === null` before either await
  // resolves; without this guard the second call would orphan the
  // first sentinel (overwritten in the ref, never released).
  const acquireInFlightRef = useRef<Promise<WakeLockSentinel | null> | null>(
    null,
  );
  // Mirrors the latest activity for the mount-only visibility watcher
  // below — the watcher's effect runs once on mount and otherwise
  // would close over a stale activity. Updating the ref each render
  // keeps `getWantLock` reading the current value.
  const activityForVisibilityRef = useRef<Activity | null>(activity);
  activityForVisibilityRef.current = activity;

  const acquireIfNeeded = useCallback(async (): Promise<void> => {
    if (wakeLockRef.current !== null && !wakeLockRef.current.released) return;
    if (acquireInFlightRef.current !== null) return;
    const pending = acquireWakeLock();
    acquireInFlightRef.current = pending;
    try {
      const sentinel = await pending;
      // Activity may have transitioned to terminal while we awaited;
      // the calling effect's cleanup is responsible for releasing if
      // so. We just publish the sentinel here.
      wakeLockRef.current = sentinel;
    } finally {
      acquireInFlightRef.current = null;
    }
  }, []);

  useEffect(() => {
    const cleanup = watchVisibilityForReacquire(
      () => wakeLockRef.current,
      (s) => {
        wakeLockRef.current = s;
      },
      () => isActiveKioskActivity(activityForVisibilityRef.current),
    );
    return () => {
      cleanup();
      // Release on unmount so we don't leak a held lock.
      const held = wakeLockRef.current;
      wakeLockRef.current = null;
      void releaseWakeLock(held);
    };
  }, []);
  useEffect(() => {
    const wantLock = isActiveKioskActivity(activity);
    if (wantLock) {
      void acquireIfNeeded();
    } else {
      const held = wakeLockRef.current;
      if (held !== null) {
        wakeLockRef.current = null;
        void releaseWakeLock(held);
      }
    }
  }, [activity, acquireIfNeeded]);

  const handleAdvance = useCallback(async (): Promise<void> => {
    if (activity === null) return;
    if (busyAdvance) return;
    setBusyAdvance(true);
    try {
      const result = await withConflictHandler({
        mutation: () => api.advance(activity.id, activity.version),
        refetch: () => refetchActivity(activity.id),
        onConflict: (conflict, fresh) => {
          useChildStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        // Route the response through the version guard rather than
        // unconditional setActivity: a newer ws envelope can land
        // during the advance round-trip, and we must NOT regress to
        // the older mutation result.
        useChildStore.getState().applyMutationResult(result);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "advance failed";
      useChildStore.getState().pushToast("error", `advance: ${message}`);
    } finally {
      setBusyAdvance(false);
    }
  }, [activity, api, busyAdvance, refetchActivity]);

  // Phase G G4: choice-driven advance. Mirrors handleAdvance but
  // posts ``{choice_index}`` so the backend can resolve the right
  // successor on a branching step. Returns "ok"/"conflict" so the
  // ChoiceButton can clear its local in-flight state without having
  // to know about VersionConflictError. Re-raises non-409 errors so
  // the button's local error indicator surfaces — the App-level
  // toast still fires for visibility too (mirrors handleAdvance's
  // top-level catch).
  const handleChoose = useCallback(
    async (choiceIndex: number): Promise<ChoiceResult> => {
      if (activity === null) {
        // The button can't be rendered without an activity, but TS
        // can't see that — fail fast rather than silently no-op so a
        // future regression where the button is mounted without an
        // activity surfaces in tests.
        throw new Error("no activity");
      }
      // Synchronous gate: if a previous choice is still in flight,
      // this tap landed in the gap between StepCard receiving the
      // ``disabled`` prop and React committing the disabled-button
      // render. Bail without firing a competing POST. We treat this
      // as "conflict" from the button's POV: no error indicator, the
      // first POST is still resolving and will route through the
      // store. (Throwing here would surface a misleading error
      // indicator on the second-tapped sibling.)
      if (choosingRef.current !== null) return "conflict";
      // Set ref + state BEFORE any await. The ref is the synchronous
      // cross-button gate (see comment on ``choosingRef``); the state
      // drives the post-commit ``disabled`` prop on each ChoiceButton
      // for the visible gate.
      choosingRef.current = choiceIndex;
      setChoosingIndex(choiceIndex);
      try {
        const result = await withConflictHandler({
          mutation: () =>
            api.advance(activity.id, activity.version, { choiceIndex }),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useChildStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useChildStore.getState().applyMutationResult(result);
          return "ok";
        }
        // ``withConflictHandler`` returns null on a 409 it caught —
        // the refetch + applyVersionConflict already fired through
        // ``onConflict``. Tell the button so it can clear in-flight.
        return "conflict";
      } catch (err) {
        // Non-conflict failure (4xx / 5xx / network). Surface the
        // global toast like handleAdvance does AND re-throw so the
        // ChoiceButton's local error indicator surfaces.
        const message = err instanceof Error ? err.message : "advance failed";
        useChildStore.getState().pushToast("error", `advance: ${message}`);
        throw err;
      } finally {
        choosingRef.current = null;
        setChoosingIndex(null);
      }
    },
    [activity, api, refetchActivity],
  );

  const showAllDone = activity !== null && isTerminalState(activity.state);
  const showActive = isActiveKioskActivity(activity);

  if (pinPromptVisible) {
    return (
      <KioskPinPrompt
        onSubmit={handlePinPromptSubmit}
        errorMessage={pinPromptError ?? undefined}
      />
    );
  }

  return (
    <main data-testid="child-root" style={FULL_BLEED_BACKGROUND_STYLE}>
      <div style={FULL_BLEED_CONTENT_STYLE}>
        {activity === null && (
          <section
            data-testid="child-idle"
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 24,
            }}
          >
            <PersonaAvatar letter="?" size={200} label="waiting" />
            <h1
              style={{
                margin: 0,
                fontSize: "clamp(1.75rem, 4vw, 3rem)",
                color: "#555",
                textAlign: "center",
              }}
            >
              Waiting for play to start...
            </h1>
          </section>
        )}
        {showActive && activity !== null && (
          <>
            <PersonaAvatar
              imagePath={avatarImage(activity)}
              letter={avatarLetter(activity)}
              size={240}
            />
            <StepCard
              activity={activity}
              onAdvance={handleAdvance}
              onChoose={handleChoose}
              advanceBusy={busyAdvance}
              choosingIndex={choosingIndex}
            />
          </>
        )}
        {showAllDone && activity !== null && (
          <section
            data-testid="child-all-done"
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 24,
            }}
          >
            <PersonaAvatar
              imagePath={avatarImage(activity)}
              letter={avatarLetter(activity)}
              size={240}
            />
            <h1
              style={{
                margin: 0,
                fontSize: "clamp(2.5rem, 6vw, 5rem)",
                color: "#1565c0",
                textAlign: "center",
              }}
            >
              All done! Great playing!
            </h1>
          </section>
        )}
        {state.toasts.length > 0 && (
          <div
            data-testid="toasts"
            style={{
              position: "absolute",
              top: 16,
              right: 16,
              display: "flex",
              flexDirection: "column",
              gap: 8,
              maxWidth: 420,
            }}
          >
            {state.toasts.map((t) => (
              <div
                key={t.id}
                role="status"
                data-toast-kind={t.kind}
                style={{
                  padding: 10,
                  background:
                    t.kind === "error"
                      ? "#fdecea"
                      : t.kind === "warning"
                        ? "#fff8e1"
                        : "#e3f2fd",
                  border: "1px solid #ddd",
                  borderRadius: 6,
                  fontSize: 14,
                }}
              >
                {t.message}
                <button
                  type="button"
                  onClick={() => useChildStore.getState().dismissToast(t.id)}
                  style={{ marginLeft: 8 }}
                >
                  dismiss
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
