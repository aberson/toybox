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
import { CapabilityBanner } from "./components/CapabilityBanner";
import { ChildProfileEditor } from "./components/ChildProfileEditor";
import { Header } from "./components/Header";
import { PinLogin } from "./components/PinLogin";
import { PinSetup } from "./components/PinSetup";
import { PlayQueueList } from "./components/PlayQueueList";
import { RoomIngestBulk } from "./components/RoomIngestBulk";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatsPanel } from "./components/StatsPanel";
import { SubTabs, Tabs, useTabState } from "./components/Tabs";
import { ToyIngest } from "./components/ToyIngest";
import { TranscriptsManager } from "./components/TranscriptsManager";
import { TriggerButton } from "./components/TriggerButton";
import { useParentStore } from "./store";
import type { Envelope } from "./ws";
import { ParentWsClient } from "./ws";

// Phase J step J8: the legacy showSuggestion / showPanel gates are
// gone — ``PlayQueueList`` owns the active-vs-proposed rendering
// internally based on the store's ``proposedList`` + ``active`` slots.

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
  // Phase J step J8: play-queue cadence + target depth seeded from
  // ``GET /api/settings/play-cadence-seconds`` and
  // ``GET /api/settings/play-target-depth`` during bootstrap. Both
  // mirror the retentionSeconds plumbing — optimistic default lives
  // in ``useState``, the fetch fills it post-login, and the value
  // threads down to ``PlayQueueList`` (cadence) and J10's
  // SettingsPanel controls (both). 30s cadence + depth 3 are the
  // backend defaults; matching them locally keeps a failed bootstrap
  // fetch from flashing odd values into the UI.
  const [playCadenceSeconds, setPlayCadenceSeconds] = useState<number>(30);
  // J10 threads playTargetDepth into the new SettingsPanel segmented
  // control. The state lives here in J8 so the bootstrap parallel-
  // fetch lands the resolved value before SettingsPanel mounts in J10
  // — wiring the consumer in J10 doesn't require touching the
  // bootstrap path again.
  const [, setPlayTargetDepth] = useState<number>(3);
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
      // Phase I step I3 + Phase J step J8: seed the transcript retention
      // preset alongside the play-queue settings + proposed-list snapshot
      // in parallel. All four are independent reads; running them
      // sequentially would cost three extra round-trips on the login
      // path. Each failure is handled independently so a single bad
      // endpoint doesn't poison the others — optimistic defaults
      // (matching the backend defaults) stay in place when a probe
      // rejects. No toast — matches the rest of the bootstrap's posture.
      const [
        retentionResult,
        cadenceResult,
        targetDepthResult,
        proposedResult,
      ] = await Promise.allSettled([
        api.getTranscriptRetention({ signal: aborter.signal }),
        api.getPlayCadenceSeconds({ signal: aborter.signal }),
        api.getPlayTargetDepth({ signal: aborter.signal }),
        api.listProposedActivities(
          { include_active: true },
          { signal: aborter.signal },
        ),
      ]);
      // ``Promise.allSettled`` swallows aborts as rejected results, so
      // a mid-bootstrap unmount (e.g. parent navigates away while these
      // four reads are in flight) would otherwise fall through to the
      // ws-client construction below. Bail explicitly when the aborter
      // fired — mirrors the ``if (isAbortError(err)) return`` guards
      // around the earlier sequential ``await``s above. Without this,
      // an aborted bootstrap leaks a live ws connection.
      if (aborter.signal.aborted) return;
      if (retentionResult.status === "fulfilled") {
        setRetentionSeconds(retentionResult.value.seconds);
      } else if (!isAbortError(retentionResult.reason)) {
        // eslint-disable-next-line no-console
        console.warn(
          "transcript retention initial fetch failed, using default",
          retentionResult.reason,
        );
      }
      if (cadenceResult.status === "fulfilled") {
        setPlayCadenceSeconds(cadenceResult.value.value);
      } else if (!isAbortError(cadenceResult.reason)) {
        // eslint-disable-next-line no-console
        console.warn(
          "play cadence initial fetch failed, using default",
          cadenceResult.reason,
        );
      }
      if (targetDepthResult.status === "fulfilled") {
        setPlayTargetDepth(targetDepthResult.value.value);
      } else if (!isAbortError(targetDepthResult.reason)) {
        // eslint-disable-next-line no-console
        console.warn(
          "play target depth initial fetch failed, using default",
          targetDepthResult.reason,
        );
      }
      if (proposedResult.status === "fulfilled") {
        // Hydrate ``proposedList`` + ``active`` from the REST snapshot.
        // Each row is routed through ``applyMutationResult`` (which
        // wraps ``applyEnvelopeToNewSlots``) so the version-guard
        // logic stays identical between REST seed and ws envelope
        // paths — a ws envelope arriving mid-fetch can't be regressed
        // by a stale snapshot.
        const store = useParentStore.getState();
        const { items, active: snapshotActive } = proposedResult.value;
        for (const item of items) {
          store.applyMutationResult(item);
        }
        if (snapshotActive !== null) {
          store.applyMutationResult(snapshotActive);
        }
      } else if (!isAbortError(proposedResult.reason)) {
        // eslint-disable-next-line no-console
        console.warn(
          "proposed activities initial fetch failed, using empty queue",
          proposedResult.reason,
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
          const cur = useParentStore.getState().active;
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

  // Phase J step J8: per-row action handlers. Each takes the target
  // ``Activity`` directly so ``PlayQueueList`` can route per-row
  // (multiple proposed cards on screen + a pinned active) without
  // needing to share a derived "current target". Per-action in-flight
  // de-duplication moved INTO ``PlayQueueList``'s busy map (keyed by
  // action + id) so two different rows can have concurrent in-flight
  // mutations without one row's spinner blocking the other.
  const handleApprove = useCallback(
    async (target: Activity): Promise<void> => {
      // Phase J step J9: switch-confirm flow. When an active activity
      // exists with a DIFFERENT id, approving a queued suggestion would
      // implicitly displace the current activity. Surface a confirm so
      // the parent acknowledges the swap; on accept, end-old → approve-
      // new in sequence, routing both results through ``applySwitch``
      // so the active slot never momentarily holds a stale row.
      // Same-id (defensive: approve on the active row) falls through to
      // the standard approve path so a no-op approve doesn't trigger a
      // confusing confirm dialog.
      const currentActive = useParentStore.getState().active;
      if (currentActive !== null && currentActive.id !== target.id) {
        const ok = window.confirm(
          `Switch from "${currentActive.title ?? "current activity"}" to "${target.title ?? "new activity"}"? The current activity will end.`,
        );
        if (!ok) return;
        const endResult = await withConflictHandler({
          mutation: () => api.end(currentActive.id, currentActive.version),
          refetch: () => refetchActivity(currentActive.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        // End failed (network rejection surfaced as throw → caller; or
        // 409 already toasted by the conflict handler). Bail before
        // firing approve so the user can retry instead of silently
        // ending up with a half-applied switch.
        if (endResult === null) return;
        const approveResult = await withConflictHandler({
          mutation: () => api.approve(target.id, target.version),
          refetch: () => refetchActivity(target.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (approveResult === null) return;
        useParentStore.getState().applySwitch(endResult, approveResult);
        return;
      }
      const result = await withConflictHandler({
        mutation: () => api.approve(target.id, target.version),
        refetch: () => refetchActivity(target.id),
        onConflict: (conflict, fresh) => {
          useParentStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        useParentStore.getState().applyMutationResult(result);
      }
    },
    [api, refetchActivity],
  );

  const handleDismiss = useCallback(
    async (target: Activity): Promise<void> => {
      const result = await withConflictHandler({
        mutation: () => api.dismiss(target.id, target.version),
        refetch: () => refetchActivity(target.id),
        onConflict: (conflict, fresh) => {
          useParentStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        // J7 fix preserved: route through ``applyMutationResult`` so
        // the dismissed-state response removes the row from the queue
        // (or clears active) synchronously rather than waiting for
        // the ws envelope.
        useParentStore.getState().applyMutationResult(result);
      }
    },
    [api, refetchActivity],
  );

  const handleRegenerate = useCallback(
    async (target: Activity): Promise<void> => {
      const result = await withConflictHandler({
        mutation: () => api.regenerate(target.id, target.version),
        refetch: () => refetchActivity(target.id),
        onConflict: (conflict, fresh) => {
          useParentStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        useParentStore.getState().applyMutationResult(result);
      }
    },
    [api, refetchActivity],
  );

  const handleEnd = useCallback(
    async (target: Activity): Promise<void> => {
      // ``ended`` is only reachable from approved/running per the
      // backend's _VALID_TRANSITIONS. From a terminal state the server
      // returns 409 invalid_transition and the panel sits there looking
      // broken. Treat End as "dismiss this panel" locally for terminal
      // states — the activity record is already finalized server-side,
      // no API call needed.
      if (target.state === "completed" || target.state === "ended") {
        useParentStore.getState().setActive(null);
        useParentStore.getState().pushToast("info", "activity ended");
        return;
      }
      try {
        const result = await withConflictHandler({
          mutation: () => api.end(target.id, target.version),
          refetch: () => refetchActivity(target.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().applyMutationResult(result);
          // The panel hides on `ended` so confirm via a toast — without
          // it the button looks like it did nothing.
          useParentStore.getState().pushToast("info", "activity ended");
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "end failed";
        useParentStore.getState().pushToast("error", `end: ${message}`);
      }
    },
    [api, refetchActivity],
  );

  const handleThumbsUp = useCallback(
    async (target: Activity): Promise<void> => {
      try {
        await api.thumbsUp(target.id);
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
    },
    [api],
  );

  const handleStepBack = useCallback(
    async (target: Activity): Promise<void> => {
      const result = await withConflictHandler({
        mutation: () => api.stepBack(target.id, target.version),
        refetch: () => refetchActivity(target.id),
        onConflict: (conflict, fresh) => {
          useParentStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        useParentStore.getState().applyMutationResult(result);
      }
    },
    [api, refetchActivity],
  );

  const handleDidntWork = useCallback(
    async (target: Activity): Promise<void> => {
      const result = await withConflictHandler({
        mutation: () =>
          api.didntWork(target.id, target.version, "parent_marked"),
        refetch: () => refetchActivity(target.id),
        onConflict: (conflict, fresh) => {
          useParentStore.getState().applyVersionConflict(conflict, fresh);
        },
      });
      if (result !== null) {
        useParentStore.getState().applyMutationResult(result);
        // The panel hides on `didnt_work` so confirm via a toast.
        useParentStore
          .getState()
          .pushToast("info", "feedback recorded — thanks");
      }
    },
    [api, refetchActivity],
  );

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
                    {/* Phase J step J8: PlayQueueList owns the
                        pinned-active vs. proposed-queue layout
                        internally. State for both slots lives in the
                        store and threads in via props; the component
                        also owns the TTL fade machinery + per-row
                        busy keys. Pre-J8 the showSuggestion /
                        showPanel gates rendered either SuggestionCard
                        OR ActivityPanel — now they coexist when both
                        slots are populated (an active card pinned
                        with proposed cards queued underneath). */}
                    <PlayQueueList
                      active={state.active}
                      proposedList={state.proposedList}
                      cadenceSeconds={playCadenceSeconds}
                      onApprove={handleApprove}
                      onDismiss={handleDismiss}
                      onRegenerate={handleRegenerate}
                      onEnd={handleEnd}
                      onStepBack={handleStepBack}
                      onDidntWork={handleDidntWork}
                      onThumbsUp={handleThumbsUp}
                    />
                    {/* Phase J step J8: TriggerButton restyled and
                        repositioned below the queue. Pre-J8 it was the
                        prominent top-of-tab CTA; with the autonomous
                        cadence loop seeding proposals, the manual
                        trigger becomes a fallback affordance. J10
                        will tighten the link-style polish. */}
                    <div style={{ marginTop: 12, textAlign: "right" }}>
                      <TriggerButton onTrigger={handleTrigger} />
                    </div>
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
