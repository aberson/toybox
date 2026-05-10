import { useCallback, useEffect, useRef, useState } from "react";
import type { JSX } from "react";

import {
  ApiClient,
  isAbortError,
  retryWithBackoff,
  withConflictHandler,
} from "./api";
import type {
  Activity,
  MetricsAudioStatus,
  ParentAuthStatus,
  ParentTokenResponse,
} from "./api";
import { ActivityPanel } from "./components/ActivityPanel";
import { CapabilityBanner } from "./components/CapabilityBanner";
import { ChildProfileEditor } from "./components/ChildProfileEditor";
import { Header } from "./components/Header";
import { OperatorTab } from "./components/OperatorTab";
import { PinLogin } from "./components/PinLogin";
import { PinSetup } from "./components/PinSetup";
import { RoomIngestBulk } from "./components/RoomIngestBulk";
import { SuggestionCard } from "./components/SuggestionCard";
import { ToyIngest } from "./components/ToyIngest";
import { TranscriptsManager } from "./components/TranscriptsManager";
import { TriggerButton } from "./components/TriggerButton";
import { useParentStore } from "./store";
import type { Envelope } from "./ws";
import { ParentWsClient } from "./ws";

// Phase A active-suggestion vs. running-activity split: the
// SuggestionCard renders for `proposed` rows; the ActivityPanel
// renders once the row is `approved`/`running`/`paused`/`completed`.
// `dismissed`, `didnt_work`, and `ended` clear the panel — `end`
// signals the parent is done with this activity, so the surface
// closes and the parent can trigger a new one.
//
// Step 23 iter-2 (L1): `paused` is included so a paused activity
// keeps the panel visible. Pause/resume are backend-only for v1 (the
// idempotency contract is wired but the explicit Pause/Resume buttons
// are deferred). Without `paused` in this set, a paused row would
// silently disappear from the parent UI.
const SUGGESTION_STATES = new Set(["proposed"]);
const PANEL_STATES = new Set(["approved", "running", "paused", "completed"]);

function deriveWsUrl(): string {
  // The vite proxy forwards /ws to the backend during dev. In prod
  // (Phase A doesn't ship one) the same path works. We deliberately
  // do NOT carry the token in the query string — it would leak into
  // referrer headers and access logs. The ws client's first message
  // is {type:"auth", token}, which the server accepts.
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

// Step 21: bootstrap modes. We probe ``/api/auth/parent/status`` first
// and pick a screen based on the response — setup if no PIN is stored,
// login otherwise. The "bootstrap" mode keeps the screen blank during
// the initial probe so neither screen flashes incorrectly.
type AuthMode = "bootstrap" | "setup" | "login" | "ready" | "error";

export function App(): JSX.Element {
  const state = useParentStore();
  // Live audio block from the metrics topic. Seeded by a one-shot
  // ``GET /api/metrics`` during bootstrap and refreshed by every
  // ``metrics`` ws envelope (~30s cadence). The header indicator +
  // mute button derive their state from this; ``null`` means we
  // haven't seen a snapshot yet (initial gray state).
  const [audioStatus, setAudioStatus] = useState<MetricsAudioStatus | null>(
    null,
  );
  const [muteBusy, setMuteBusy] = useState(false);
  const [showChildEditor, setShowChildEditor] = useState(false);
  const [showToyIngest, setShowToyIngest] = useState(false);
  const [showRoomIngest, setShowRoomIngest] = useState(false);
  const [showOperator, setShowOperator] = useState(false);
  const [showTranscripts, setShowTranscripts] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("bootstrap");
  const [authStatus, setAuthStatus] = useState<ParentAuthStatus | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const apiRef = useRef<ApiClient | null>(null);
  const wsRef = useRef<ParentWsClient | null>(null);
  // Step 24: registry of metrics-envelope listeners. The ws onEnvelope
  // callback fans every metrics envelope out to every listener; the
  // OperatorTab subscribes via a callback returned from
  // ``subscribeToMetrics``. Built as a Set so unsubscribe is O(1).
  const metricsListenersRef = useRef<Set<(env: Envelope) => void>>(new Set());
  const subscribeToMetrics = useCallback(
    (handler: (env: Envelope) => void): (() => void) => {
      metricsListenersRef.current.add(handler);
      return () => {
        metricsListenersRef.current.delete(handler);
      };
    },
    [],
  );

  // Same pattern for live ``transcript`` envelopes — TranscriptsManager
  // subscribes so a new utterance appears in the list without the user
  // refreshing the panel. Without this, ``applyEnvelope`` would drop
  // transcript envelopes on the floor (the store has no transcript
  // slot) and the only way to see new rows would be hide/show.
  const transcriptListenersRef = useRef<Set<(env: Envelope) => void>>(new Set());
  const subscribeToTranscripts = useCallback(
    (handler: (env: Envelope) => void): (() => void) => {
      transcriptListenersRef.current.add(handler);
      return () => {
        transcriptListenersRef.current.delete(handler);
      };
    },
    [],
  );

  // Lazily build the api client once. getToken pulls from the store on
  // every call so token rotation surfaces immediately.
  if (apiRef.current === null) {
    apiRef.current = new ApiClient({
      getToken: () => useParentStore.getState().token,
    });
  }
  const api = apiRef.current;

  // Step 21 bootstrap: probe ``GET /api/auth/parent/status`` first so
  // the UI can decide between PinSetup (first-run) and PinLogin
  // (recurring). The token-issuance + health + ws bring-up only run
  // AFTER the user clears the PIN gate (via ``onAuthSuccess`` below).
  // The AbortController is constructed inside the effect body so each
  // mount (including StrictMode's deliberate double-mount) gets a
  // fresh, un-aborted controller — reusing a ref-cached controller
  // across mounts would cause the second mount's fetches to throw
  // AbortError synchronously and the UI to stick on bootstrap.
  // ``aborterRef`` still mirrors the live controller so the
  // ``continueBootstrap`` continuation (called from a child
  // component's onSuccess after the effect already returned) can
  // share the same abort signal.
  const aborterRef = useRef<AbortController | null>(null);

  // Continuation: run after a PIN flow successfully mints a token.
  // Stores the token, fetches health, and starts the ws client.
  const continueBootstrap = useCallback(
    async (tokenResp: ParentTokenResponse): Promise<void> => {
      // If the effect already cleaned up (e.g. parent unmount mid-flow)
      // there is no live controller — fall back to an inert one so the
      // fetches are still cancellable but never hit a null deref.
      const aborter = aborterRef.current ?? new AbortController();
      useParentStore.getState().setToken(tokenResp);
      setAuthMode("ready");
      try {
        const health = await api.getHealth({ signal: aborter.signal });
        useParentStore.getState().setHealth(health);
      } catch (err) {
        if (isAbortError(err)) return;
        // health is best-effort during bootstrap
      }
      // Seed the audio block so the header indicator paints with a real
      // state on first render rather than waiting up to 30s for the
      // first metrics ws envelope. Failure is non-fatal — the header
      // stays gray until a ws envelope arrives.
      try {
        const snap = await api.getMetrics({ signal: aborter.signal });
        setAudioStatus(snap.audio);
      } catch (err) {
        if (isAbortError(err)) return;
        // metrics is best-effort during bootstrap
      }
      const ws = new ParentWsClient({
        url: deriveWsUrl(),
        getToken: () => useParentStore.getState().token,
        onEnvelope: (env: Envelope) => {
          useParentStore.getState().applyEnvelope(env);
          if (env.topic === "metrics") {
            // Update the header's live audio state from every metrics
            // envelope. Defensive cast: the wire shape is checked by
            // the publisher but a malformed payload should not crash
            // the ws callback.
            const payload = env.payload as { audio?: MetricsAudioStatus };
            if (payload.audio !== undefined) {
              setAudioStatus(payload.audio);
            }
            for (const listener of metricsListenersRef.current) {
              try {
                listener(env);
              } catch {
                // listener bugs must not crash the ws callback.
              }
            }
          }
          if (env.topic === "transcript") {
            for (const listener of transcriptListenersRef.current) {
              try {
                listener(env);
              } catch {
                // listener bugs must not crash the ws callback.
              }
            }
          }
        },
        onState: (s) => {
          useParentStore.getState().setWsState(s);
        },
        onRejected: (rejected) => {
          useParentStore.getState().applyRejectedTopics(rejected);
        },
        onReconnect: () => {
          const cur = useParentStore.getState().activity;
          if (cur === null) return;
          void api
            .getActivity(cur.id, { signal: aborter.signal })
            .then((fresh) => {
              useParentStore.getState().applyReconnectResync(fresh);
            })
            .catch((err: unknown) => {
              if (isAbortError(err)) return;
              const message =
                err instanceof Error ? err.message : "reconnect refetch failed";
              useParentStore
                .getState()
                .pushToast("warning", `reconnect: ${message}`);
            });
        },
      });
      wsRef.current = ws;
      ws.start();
    },
    [api],
  );

  useEffect(() => {
    // Fresh controller per mount — StrictMode-safe. Abort on cleanup
    // and clear the ref so the next mount starts with no controller
    // until this effect body runs again.
    const aborter = new AbortController();
    aborterRef.current = aborter;
    let mounted = true;
    const probeStatus = async (): Promise<void> => {
      try {
        const status = await retryWithBackoff(() =>
          api.getAuthStatus({ signal: aborter.signal }),
        );
        if (!mounted) return;
        setAuthStatus(status);
        setAuthMode(status.pin_set ? "login" : "setup");
      } catch (err) {
        if (!mounted || isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "status probe failed";
        setBootstrapError(message);
        setAuthMode("error");
      }
    };
    void probeStatus();
    return () => {
      mounted = false;
      aborter.abort();
      // Clear the ref so a stale (already-aborted) controller can't
      // leak into a follow-on remount via ``continueBootstrap``.
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
      wsRef.current?.stop();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // PIN-flow success handler: invoked by either PinSetup or PinLogin
  // when the backend returns a token. Kicks the rest of the bootstrap
  // (health probe + ws) via ``continueBootstrap``.
  const handleAuthSuccess = useCallback(
    (tokenResp: ParentTokenResponse): void => {
      void continueBootstrap(tokenResp).catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "bootstrap failed";
        useParentStore.getState().pushToast("error", `bootstrap: ${message}`);
      });
    },
    [continueBootstrap],
  );

  // Header mic indicator + mute toggle, derived from the live audio
  // block in the metrics envelope. Three states:
  //   * audioStatus null               — gray "paused" (pre-snapshot)
  //   * mic_device == null             — red "error" (capture daemon down)
  //   * mic_enabled == false           — gray "paused" (parent muted)
  //   * else                           — green "capturing"
  const micState: "capturing" | "paused" | "error" = (() => {
    if (audioStatus === null) return "paused";
    if (audioStatus.mic_device === null) return "error";
    if (!audioStatus.mic_enabled) return "paused";
    return "capturing";
  })();
  const muted = audioStatus !== null && !audioStatus.mic_enabled;

  const handleToggleMute = useCallback((): void => {
    if (muteBusy) return;
    if (audioStatus === null) return; // can't toggle before snapshot lands
    const next = !muted;
    setMuteBusy(true);
    // Optimistic local update so the indicator + button label flip
    // immediately. Reconciled by the next metrics envelope.
    setAudioStatus((prev) =>
      prev !== null ? { ...prev, mic_enabled: !next } : prev,
    );
    api
      .setMicEnabled(!next)
      .then((resp) => {
        setAudioStatus((prev) =>
          prev !== null ? { ...prev, mic_enabled: resp.enabled } : prev,
        );
        setMuteBusy(false);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          setMuteBusy(false);
          return;
        }
        // Revert the optimistic flip.
        setAudioStatus((prev) =>
          prev !== null ? { ...prev, mic_enabled: !muted } : prev,
        );
        const message =
          err instanceof Error ? err.message : "toggle mute failed";
        useParentStore.getState().pushToast("error", `mute: ${message}`);
        setMuteBusy(false);
      });
  }, [api, audioStatus, muteBusy, muted]);

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

  // Per-action in-flight guards. Two rapid clicks on (e.g.) approve
  // would otherwise fire mutations with the same If-Match-Version and
  // the second would 409. We disable the buttons while a request is
  // in flight per action.
  const [busy, setBusy] = useState({
    approve: false,
    skip: false,
    dismiss: false,
    regenerate: false,
    end: false,
    didntWork: false,
    thumbsUp: false,
    stepBack: false,
  });

  type BusyKey = keyof typeof busy;
  const runGuarded = useCallback(
    async (key: BusyKey, fn: () => Promise<void>): Promise<void> => {
      if (busy[key]) return;
      setBusy((prev) => ({ ...prev, [key]: true }));
      try {
        await fn();
      } finally {
        setBusy((prev) => ({ ...prev, [key]: false }));
      }
    },
    [busy],
  );

  const handleTrigger = useCallback(async (): Promise<void> => {
    try {
      const now = new Date();
      const seed = Math.floor(Math.random() * 1_000_000);
      const activity = await api.propose({
        intent: "request_play",
        slot: "freeplay",
        hour: now.getHours(),
        seed,
      });
      // Version-guarded set so a same-id ws envelope can't be regressed
      // by a slow propose response landing late.
      useParentStore.getState().applyMutationResult(activity);
    } catch (err) {
      const message = err instanceof Error ? err.message : "trigger failed";
      useParentStore.getState().pushToast("error", `trigger: ${message}`);
    }
  }, [api]);

  const activity = state.activity;

  const handleApprove = useCallback(
    () =>
      runGuarded("approve", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.approve(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleSkip = useCallback(
    () =>
      runGuarded("skip", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.regenerate(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleDismiss = useCallback(
    () =>
      runGuarded("dismiss", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.dismiss(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().setActivity(null);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleRegenerate = useCallback(
    () =>
      runGuarded("regenerate", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.regenerate(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleEnd = useCallback(
    () =>
      runGuarded("end", async () => {
        if (activity === null) return;
        // ``ended`` is only reachable from approved/running per the
        // backend's _VALID_TRANSITIONS. From a terminal state (most
        // commonly ``completed`` after the kiosk advanced through the
        // last step) the server returns 409 invalid_transition and the
        // panel sits there looking broken. Treat End as "dismiss this
        // panel" locally for terminal states — the activity record is
        // already finalized server-side, no API call needed.
        if (activity.state === "completed" || activity.state === "ended") {
          useParentStore.getState().setActivity(null);
          useParentStore.getState().pushToast("info", "activity ended");
          return;
        }
        try {
          const result = await withConflictHandler({
            mutation: () => api.end(activity.id, activity.version),
            refetch: () => refetchActivity(activity.id),
            onConflict: (conflict, fresh) => {
              useParentStore.getState().applyVersionConflict(conflict, fresh);
            },
          });
          if (result !== null) {
            useParentStore.getState().applyMutationResult(result);
            // The panel hides on `ended` (not in PANEL_STATES), so confirm
            // the action took effect — without a toast it looks like the
            // button did nothing.
            useParentStore.getState().pushToast("info", "activity ended");
          }
        } catch (err) {
          // Non-version-conflict errors (e.g. invalid_transition, network)
          // would otherwise surface as a silent unhandled rejection.
          const message = err instanceof Error ? err.message : "end failed";
          useParentStore.getState().pushToast("error", `end: ${message}`);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleThumbsUp = useCallback(
    () =>
      runGuarded("thumbsUp", async () => {
        if (activity === null) return;
        try {
          await api.thumbsUp(activity.id);
          useParentStore
            .getState()
            .pushToast("info", "thanks — feedback recorded");
        } catch (err) {
          const message =
            err instanceof Error ? err.message : "thumbs-up failed";
          useParentStore
            .getState()
            .pushToast("error", `thumbs-up: ${message}`);
        }
      }),
    [activity, api, runGuarded],
  );

  const handleStepBack = useCallback(
    () =>
      runGuarded("stepBack", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.stepBack(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleDidntWork = useCallback(
    () =>
      runGuarded("didntWork", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () =>
            api.didntWork(activity.id, activity.version, "parent_marked"),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
          // The panel hides on `didnt_work` (not in PANEL_STATES), so without
          // a toast the action looks like it did nothing. Confirm the
          // feedback was recorded.
          useParentStore
            .getState()
            .pushToast("info", "feedback recorded — thanks");
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const showSuggestion =
    activity !== null && SUGGESTION_STATES.has(activity.state);
  const showPanel = activity !== null && PANEL_STATES.has(activity.state);

  // Step 21: gate on PIN status before rendering the main app.
  if (authMode === "bootstrap") {
    return (
      <main
        data-testid="pin-bootstrap"
        style={{ fontFamily: "system-ui, sans-serif" }}
      />
    );
  }
  if (authMode === "error") {
    return (
      <main
        data-testid="pin-bootstrap-error"
        style={{ fontFamily: "system-ui, sans-serif", padding: 16 }}
      >
        <p>Could not contact the toybox backend.</p>
        {bootstrapError !== null && (
          <pre style={{ fontSize: 12 }}>{bootstrapError}</pre>
        )}
        <button
          type="button"
          onClick={() => window.location.reload()}
          data-testid="pin-bootstrap-retry"
        >
          Retry
        </button>
      </main>
    );
  }
  if (authMode === "setup") {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif" }}>
        <PinSetup api={api} onSuccess={handleAuthSuccess} />
      </main>
    );
  }
  if (authMode === "login") {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif" }}>
        <PinLogin
          api={api}
          initialLockSeconds={
            authStatus !== null && authStatus.locked
              ? authStatus.seconds_until_unlock
              : 0
          }
          onSuccess={handleAuthSuccess}
        />
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720 }}>
      <Header
        micState={micState}
        muted={muted}
        onToggleMute={handleToggleMute}
        wsState={state.wsState}
      />
      <CapabilityBanner reason={state.capabilityReason} />
      <div style={{ padding: 16 }}>
        <div style={{ marginBottom: 16, display: "flex", gap: 8 }}>
          <TriggerButton onTrigger={handleTrigger} />
          <button
            type="button"
            data-testid="toggle-child-editor"
            aria-pressed={showChildEditor}
            onClick={() => setShowChildEditor((prev) => !prev)}
          >
            {showChildEditor ? "hide child profiles" : "child profiles"}
          </button>
          <button
            type="button"
            data-testid="toggle-toy-ingest"
            aria-pressed={showToyIngest}
            onClick={() => setShowToyIngest((prev) => !prev)}
          >
            {showToyIngest ? "hide toys" : "toys"}
          </button>
          <button
            type="button"
            data-testid="toggle-room-ingest"
            aria-pressed={showRoomIngest}
            onClick={() => setShowRoomIngest((prev) => !prev)}
          >
            {showRoomIngest ? "hide rooms" : "rooms"}
          </button>
          <button
            type="button"
            data-testid="toggle-operator"
            aria-pressed={showOperator}
            onClick={() => setShowOperator((prev) => !prev)}
          >
            {showOperator ? "hide operator" : "operator"}
          </button>
          <button
            type="button"
            data-testid="toggle-transcripts"
            aria-pressed={showTranscripts}
            onClick={() => setShowTranscripts((prev) => !prev)}
          >
            {showTranscripts ? "hide transcripts" : "transcripts"}
          </button>
        </div>
        {showChildEditor && <ChildProfileEditor api={api} /> }
        {showToyIngest && <ToyIngest api={api} />}
        {showRoomIngest && <RoomIngestBulk api={api} />}
        {showOperator && (
          <OperatorTab api={api} subscribeToMetrics={subscribeToMetrics} />
        )}
        {showTranscripts && (
          <TranscriptsManager
            api={api}
            subscribeToTranscripts={subscribeToTranscripts}
          />
        )}
        {showSuggestion && activity !== null && (
          <SuggestionCard
            activity={activity}
            onApprove={handleApprove}
            onSkip={handleSkip}
            onDismiss={handleDismiss}
            busy={{
              approve: busy.approve,
              skip: busy.skip,
              dismiss: busy.dismiss,
            }}
          />
        )}
        {showPanel && activity !== null && (
          <ActivityPanel
            activity={activity}
            onRegenerate={handleRegenerate}
            onEnd={handleEnd}
            onDidntWork={handleDidntWork}
            onThumbsUp={handleThumbsUp}
            onStepBack={handleStepBack}
            busy={{
              regenerate: busy.regenerate,
              end: busy.end,
              didntWork: busy.didntWork,
              thumbsUp: busy.thumbsUp,
              stepBack: busy.stepBack,
            }}
          />
        )}
        {state.toasts.length > 0 && (
          <div data-testid="toasts" style={{ marginTop: 16 }}>
            {state.toasts.map((t) => (
              <div
                key={t.id}
                role="status"
                data-toast-kind={t.kind}
                style={{
                  padding: 8,
                  margin: "4px 0",
                  background:
                    t.kind === "error"
                      ? "#fdecea"
                      : t.kind === "warning"
                        ? "#fff8e1"
                        : "#e3f2fd",
                  border: "1px solid #ddd",
                  borderRadius: 4,
                  fontSize: 13,
                }}
              >
                {t.message}
                <button
                  type="button"
                  onClick={() => useParentStore.getState().dismissToast(t.id)}
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
