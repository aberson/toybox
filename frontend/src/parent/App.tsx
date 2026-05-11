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
import { PinLogin } from "./components/PinLogin";
import { PinSetup } from "./components/PinSetup";
import { RoomIngestBulk } from "./components/RoomIngestBulk";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatsPanel } from "./components/StatsPanel";
import { SubTabs, Tabs, useTabState } from "./components/Tabs";
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

// Phase H step H2: top-level tab + per-section sub-tab unions. Each tab
// dimension persists independently in localStorage so a re-mount lands
// the operator on their last selection. Storage keys + defaults match
// the plan's Vocabulary section. Kept as string-literal unions (not
// enums) so the values themselves double as ``data-testid`` suffixes.
type TopTab = "play" | "kids-toyboxes" | "settings";
type PlaySubTab = "play-ideas" | "transcription";
type KidsSubTab = "toys" | "children" | "rooms";
type SettingsSubTab = "settings" | "stats";

const TOP_TAB_VALUES: readonly TopTab[] = ["play", "kids-toyboxes", "settings"];
const PLAY_SUBTAB_VALUES: readonly PlaySubTab[] = ["play-ideas", "transcription"];
const KIDS_SUBTAB_VALUES: readonly KidsSubTab[] = ["toys", "children", "rooms"];
const SETTINGS_SUBTAB_VALUES: readonly SettingsSubTab[] = ["settings", "stats"];

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
  // Phase I step I3: household-scoped transcript retention preset
  // (seconds). Seeded from ``GET /api/settings/transcript-retention``
  // during bootstrap (mirrors the listening-mode + mic seeding from
  // /api/metrics); on fetch failure the optimistic default ``60``
  // (which matches the backend default) is retained and a warn is
  // logged. SettingsPanel writes update this state via the
  // ``onRetentionChanged`` callback; the value flows down to
  // TranscriptsManager so the I4 fade machinery uses the same source-
  // of-truth as the picker. No WS broadcast — single-parent kiosk.
  const [retentionSeconds, setRetentionSeconds] = useState<number>(60);
  const [authMode, setAuthMode] = useState<AuthMode>("bootstrap");
  const [authStatus, setAuthStatus] = useState<ParentAuthStatus | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const apiRef = useRef<ApiClient | null>(null);
  const wsRef = useRef<ParentWsClient | null>(null);

  // Phase H step H2: top + sub-tab selections live in localStorage so a
  // re-mount lands the operator where they left off. The four
  // dimensions are independent — flipping the top tab doesn't reset the
  // remembered sub-tab inside the OTHER top tabs. H3/H5 will read these
  // same hooks when they fill in their placeholders.
  const topTab = useTabState<TopTab>(
    "toybox.parent.tabs.top",
    "play",
    TOP_TAB_VALUES,
  );
  const playTab = useTabState<PlaySubTab>(
    "toybox.parent.tabs.play",
    "play-ideas",
    PLAY_SUBTAB_VALUES,
  );
  const kidsTab = useTabState<KidsSubTab>(
    "toybox.parent.tabs.kids-toyboxes",
    "toys",
    KIDS_SUBTAB_VALUES,
  );
  const settingsTab = useTabState<SettingsSubTab>(
    "toybox.parent.tabs.settings",
    "settings",
    SETTINGS_SUBTAB_VALUES,
  );

  // Live ``transcript`` envelope fanout — TranscriptsManager
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

  // Phase H step H5: live ``metrics`` envelope fanout — StatsPanel
  // subscribes so the operator-dashboard tables refresh from the same
  // 30s ws cadence the old OperatorTab used. H2 removed the fanout when
  // OperatorTab was unmounted in the placeholder; H5 restores it for
  // the new StatsPanel under Settings → Stats. The header's audio
  // indicator continues to read audio off every envelope directly
  // (below in onEnvelope) regardless of this fanout.
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
      // Phase I step I3: seed the transcript retention preset alongside
      // the audio/listening seeding. Failure is non-fatal — the
      // optimistic default of 60s (matching the backend default + the
      // ``useState`` initializer) stays in place and the next
      // SettingsPanel write succeeds against the server's authoritative
      // value. No toast — matches the rest of the bootstrap's posture.
      try {
        const retentionResp = await api.getTranscriptRetention({
          signal: aborter.signal,
        });
        setRetentionSeconds(retentionResp.seconds);
      } catch (err) {
        if (isAbortError(err)) return;
        // eslint-disable-next-line no-console
        console.warn(
          "transcript retention initial fetch failed, using default",
          err,
        );
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
            // Phase H step H5: fan out to any StatsPanel subscriber.
            // The header's audioStatus still updates directly off
            // every envelope (above) so the mic indicator stays live
            // even when Stats isn't mounted.
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
        {/* Phase H step H2: two-level tab shell. The top Tabs row picks
            the section; each section renders its own SubTabs row inside
            its tabpanel. State for which tab is selected lives in
            ``useTabState`` (localStorage-backed) so the operator's last
            choice survives a reload. Kids & Toyboxes + Settings are
            placeholders in this step — H3 wires in the children/toys/
            rooms editors, H5 wires in the settings/stats panels. */}
        <Tabs<TopTab>
          items={[
            { key: "play", label: "Play" },
            { key: "kids-toyboxes", label: "Kids & Toyboxes" },
            { key: "settings", label: "Settings" },
          ]}
          value={topTab.value}
          onChange={topTab.setValue}
        />
        <div role="tabpanel" style={{ marginTop: 16 }}>
          {topTab.value === "play" && (
            <>
              <SubTabs<PlaySubTab>
                items={[
                  { key: "play-ideas", label: "Play Ideas" },
                  { key: "transcription", label: "Transcription" },
                ]}
                value={playTab.value}
                onChange={playTab.setValue}
              />
              <div role="tabpanel" style={{ marginTop: 12 }}>
                {playTab.value === "play-ideas" && (
                  <>
                    <div style={{ marginBottom: 16 }}>
                      <TriggerButton onTrigger={handleTrigger} />
                    </div>
                    {/* Activity surface gates preserved from pre-H2 —
                        the SuggestionCard renders for ``proposed`` rows
                        and ActivityPanel renders for the running/paused/
                        completed lifecycle. State is unaffected by tab
                        switches; only render visibility is gated by the
                        ``play`` + ``play-ideas`` selection. */}
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
                  </>
                )}
                {playTab.value === "transcription" && (
                  <TranscriptsManager
                    api={api}
                    subscribeToTranscripts={subscribeToTranscripts}
                    retentionSeconds={retentionSeconds}
                  />
                )}
              </div>
            </>
          )}
          {topTab.value === "kids-toyboxes" && (
            <>
              <SubTabs<KidsSubTab>
                items={[
                  { key: "toys", label: "Toys" },
                  { key: "children", label: "Children" },
                  { key: "rooms", label: "Rooms" },
                ]}
                value={kidsTab.value}
                onChange={kidsTab.setValue}
              />
              <div role="tabpanel" style={{ marginTop: 12 }}>
                {/* H3: relocated ToyIngest / ChildProfileEditor /
                    RoomIngestBulk under their respective sub-tabs. The
                    components are mounted unchanged from their pre-H2
                    homes — only their parent gate moved. H5 stripped the
                    banned-themes UI from ChildProfileEditor and promoted
                    it to the global Settings → Settings sub-tab. */}
                {kidsTab.value === "toys" && <ToyIngest api={api} />}
                {kidsTab.value === "children" && (
                  <ChildProfileEditor api={api} />
                )}
                {kidsTab.value === "rooms" && <RoomIngestBulk api={api} />}
              </div>
            </>
          )}
          {topTab.value === "settings" && (
            <>
              <SubTabs<SettingsSubTab>
                items={[
                  { key: "settings", label: "Settings" },
                  { key: "stats", label: "Stats" },
                ]}
                value={settingsTab.value}
                onChange={settingsTab.setValue}
              />
              <div role="tabpanel" style={{ marginTop: 12 }}>
                {/* H5: SettingsPanel hosts the toggle cards + the new
                    global banned-themes editor; StatsPanel renders the
                    metrics snapshot dashboard the pre-H5 OperatorTab
                    used to surface. Only the visible sub-tab is
                    mounted so the metrics-snapshot fetch + ws
                    subscription only fire when Stats is selected. */}
                {settingsTab.value === "settings" && (
                  <SettingsPanel
                    api={api}
                    currentRetentionSeconds={retentionSeconds}
                    onRetentionChanged={setRetentionSeconds}
                  />
                )}
                {settingsTab.value === "stats" && (
                  <StatsPanel
                    api={api}
                    subscribeToMetrics={subscribeToMetrics}
                  />
                )}
              </div>
            </>
          )}
        </div>
        {/* Toasts are cross-cutting transient feedback — they MUST stay
            outside any tab gate so a mute-error toast (Settings-driven)
            is visible from Play, etc. */}
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
