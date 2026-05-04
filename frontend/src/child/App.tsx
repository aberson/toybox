import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, JSX } from "react";

import {
  ApiClient,
  isAbortError,
  retryWithBackoff,
  withConflictHandler,
} from "./api";
import type { Activity } from "./api";
import { NextStepButton } from "./components/NextStepButton";
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
import { ChildWsClient } from "./ws";
import type { Envelope } from "./ws";

// Mirrors parent App's deriveWsUrl. The token rides in-band as the
// first ws message, never in the URL.
function deriveWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

const FULL_BLEED_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  margin: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 32,
  background: "linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)",
  fontFamily: "system-ui, sans-serif",
  padding: 32,
  boxSizing: "border-box",
  overflow: "hidden",
};

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
        // Step 21: ``/api/auth/parent`` is now PIN-gated. The kiosk
        // does not own the PIN — production will hand the kiosk a
        // pre-issued token via the (yet-to-ship) kiosk pairing flow.
        // Until then, the bootstrap PIN is read from
        // ``window.__TOYBOX_KIOSK_PIN__`` (set by the test harness or
        // the operator's local config); a 401/423 response here is
        // surfaced as a bootstrap toast so the kiosk doesn't silently
        // hang. Retry-with-backoff still applies for transient 5xx /
        // network blips.
        const kioskWindow = window as unknown as {
          __TOYBOX_KIOSK_PIN__?: string;
        };
        const bootstrapPin = kioskWindow.__TOYBOX_KIOSK_PIN__ ?? "";
        const tokenResp = await retryWithBackoff(() =>
          api.issueParentToken({ pin: bootstrapPin }, { signal: aborter.signal }),
        );
        if (!mounted) return;
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

  const showAllDone = activity !== null && isTerminalState(activity.state);
  const showActive =
    activity !== null &&
    !showAllDone &&
    (activity.state === "approved" || activity.state === "running");

  return (
    <main data-testid="child-root" style={FULL_BLEED_STYLE}>
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
          <StepCard activity={activity} />
          <NextStepButton onClick={handleAdvance} busy={busyAdvance} />
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
    </main>
  );
}
