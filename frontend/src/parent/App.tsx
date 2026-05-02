import { useCallback, useEffect, useRef, useState } from "react";
import type { JSX } from "react";

import { ApiClient, isAbortError, withConflictHandler } from "./api";
import type { Activity } from "./api";
import { ActivityPanel } from "./components/ActivityPanel";
import { CapabilityBanner } from "./components/CapabilityBanner";
import { Header } from "./components/Header";
import { SuggestionCard } from "./components/SuggestionCard";
import { TriggerButton } from "./components/TriggerButton";
import { useParentStore } from "./store";
import type { Envelope } from "./ws";
import { ParentWsClient } from "./ws";

// Phase A active-suggestion vs. running-activity split: the
// SuggestionCard renders for `proposed` rows; the ActivityPanel
// renders once the row is `approved`/`running`/`completed`/`ended`.
// `dismissed` and `didnt_work` clear the panel.
const SUGGESTION_STATES = new Set(["proposed"]);
const PANEL_STATES = new Set(["approved", "running", "completed", "ended"]);

function deriveWsUrl(): string {
  // The vite proxy forwards /ws to the backend during dev. In prod
  // (Phase A doesn't ship one) the same path works. We deliberately
  // do NOT carry the token in the query string — it would leak into
  // referrer headers and access logs. The ws client's first message
  // is {type:"auth", token}, which the server accepts.
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

export function App(): JSX.Element {
  const state = useParentStore();
  const [muted, setMuted] = useState(false);
  const apiRef = useRef<ApiClient | null>(null);
  const wsRef = useRef<ParentWsClient | null>(null);

  // Lazily build the api client once. getToken pulls from the store on
  // every call so token rotation surfaces immediately.
  if (apiRef.current === null) {
    apiRef.current = new ApiClient({
      getToken: () => useParentStore.getState().token,
    });
  }
  const api = apiRef.current;

  // Phase A bootstrap: issue a parent token, fetch /api/health, open
  // ws. AbortController binds in-flight fetches to this effect's
  // lifetime; StrictMode's double-effect cleanup will cancel them and
  // also stop any ws client started by the first run before the second
  // attempts to start its own.
  useEffect(() => {
    const aborter = new AbortController();
    let mounted = true;
    const boot = async (): Promise<void> => {
      try {
        const tokenResp = await api.issueParentToken({ signal: aborter.signal });
        if (!mounted) return;
        useParentStore.getState().setToken(tokenResp);
        try {
          const health = await api.getHealth({ signal: aborter.signal });
          if (!mounted) return;
          useParentStore.getState().setHealth(health);
        } catch (err) {
          if (isAbortError(err)) return;
          // health is best-effort during bootstrap
        }
        if (!mounted) return;
        const ws = new ParentWsClient({
          url: deriveWsUrl(),
          // Pull a fresh token on every (re)connect so reconnect after
          // a TTL roll picks up the new value rather than reusing the
          // captured-at-construction one.
          getToken: () => useParentStore.getState().token,
          onEnvelope: (env: Envelope) => {
            useParentStore.getState().applyEnvelope(env);
          },
          onState: (s) => {
            useParentStore.getState().setWsState(s);
          },
          onRejected: (rejected) => {
            useParentStore.getState().applyRejectedTopics(rejected);
          },
          onReconnect: () => {
            // On reconnect: refetch the active activity if we have one,
            // so we don't drift past missed envelopes.
            const cur = useParentStore.getState().activity;
            if (cur === null) return;
            void api
              .getActivity(cur.id, { signal: aborter.signal })
              .then((fresh) => {
                if (!mounted) return;
                useParentStore.getState().setActivity(fresh);
              })
              .catch((err: unknown) => {
                if (isAbortError(err)) return;
                // Keep what we have; the ws stream will catch us up.
              });
          },
        });
        wsRef.current = ws;
        ws.start();
      } catch (err) {
        if (!mounted || isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "bootstrap failed";
        useParentStore.getState().pushToast("error", `bootstrap: ${message}`);
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

  // Phase A has no real capture pipeline. The mic indicator stays at
  // "paused" until something declares actual capture. The mute toggle
  // is a future-shape stub: it flips the button label and aria-pressed
  // but does NOT light up the green-capturing dot. Phase B Step 11
  // wires real capture; that's when the indicator switches to
  // "capturing" — and only when audio is actually flowing.

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
      useParentStore.getState().setActivity(activity);
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
          useParentStore.getState().setActivity(result);
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
          useParentStore.getState().setActivity(result);
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
          useParentStore.getState().setActivity(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const handleEnd = useCallback(
    () =>
      runGuarded("end", async () => {
        if (activity === null) return;
        const result = await withConflictHandler({
          mutation: () => api.end(activity.id, activity.version),
          refetch: () => refetchActivity(activity.id),
          onConflict: (conflict, fresh) => {
            useParentStore.getState().applyVersionConflict(conflict, fresh);
          },
        });
        if (result !== null) {
          useParentStore.getState().setActivity(result);
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
          useParentStore.getState().setActivity(result);
        }
      }),
    [activity, api, refetchActivity, runGuarded],
  );

  const showSuggestion =
    activity !== null && SUGGESTION_STATES.has(activity.state);
  const showPanel = activity !== null && PANEL_STATES.has(activity.state);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720 }}>
      <Header
        micState={state.micState}
        muted={muted}
        onToggleMute={() => setMuted((prev) => !prev)}
        wsState={state.wsState}
      />
      <CapabilityBanner reason={state.capabilityReason} />
      <div style={{ padding: 16 }}>
        <div style={{ marginBottom: 16 }}>
          <TriggerButton onTrigger={handleTrigger} />
        </div>
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
            busy={{
              regenerate: busy.regenerate,
              end: busy.end,
              didntWork: busy.didntWork,
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
