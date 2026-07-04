// Phase J step J8 reviewer-finding regression tests for the parallel
// bootstrap path.
//
// continueBootstrap() in App.tsx fires reads in parallel via
// ``Promise.allSettled``:
//   1. /api/settings/transcript-retention
//   2. /api/settings/play-target-depth
//   3. /api/activities/proposed?include_active=true
//   ... plus feature flags
// Then constructs a ParentWsClient and calls start().
//
// Two regressions we pin here:
//
// (A) Happy path — all reads resolve, the resolved values are lifted
//     into the right places: ``proposedList`` + ``active`` into the
//     parent store; ``playTargetDepth`` lifted to state.
//     ``cadenceSeconds`` is now a fixed 0 (disabled) — no longer
//     fetched from the backend.
//
// (B) Abort halts WS — if the aborter fires mid-bootstrap (e.g. the
//     parent unmounts), ``Promise.allSettled`` swallows the abort as
//     a rejected result and the code would fall through to
//     ``new ParentWsClient(...)`` without an explicit ``aborter.signal.
//     aborted`` guard. The fix adds that guard; this test pins it by
//     asserting the spied ParentWsClient constructor is NOT called
//     when the aborter is fired before the parallel fetches resolve.
//
// The PlayQueueList + ParentWsClient stubs are scoped via ``vi.mock``
// at the top of the file. Mirrors the pattern used in
// App.retention.test.tsx (which stubs SettingsPanel + TranscriptsManager
// the same way).

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture every render of the stubbed PlayQueueList so tests can
// inspect the latest prop pass (cadenceSeconds, proposedList, active).
const playQueueListPropsLog: Array<Record<string, unknown>> = [];

vi.mock("./components/PlayQueueList", () => ({
  PlayQueueList: (props: Record<string, unknown>) => {
    playQueueListPropsLog.push(props);
    return <div data-testid="play-queue-list-stub" />;
  },
}));

// Track every ParentWsClient construction. The mock returns a no-op
// stub so ``ws.start()`` doesn't actually try to open a socket
// (happy-dom's WebSocket would fail anyway, but the constructor side-
// effect is what we want to observe).
const parentWsClientCalls: Array<{ url: string }> = [];

vi.mock("./ws", async () => {
  const actual = await vi.importActual<typeof import("./ws")>("./ws");
  return {
    ...actual,
    ParentWsClient: vi.fn().mockImplementation((opts: { url: string }) => {
      parentWsClientCalls.push({ url: opts.url });
      return {
        start: vi.fn(),
        stop: vi.fn(),
      };
    }),
  };
});

// Late imports so the vi.mock factories above apply first.
import { App } from "./App";
import { useParentStore } from "./store";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  playQueueListPropsLog.length = 0;
  parentWsClientCalls.length = 0;
});

interface FetchOpts {
  retentionSeconds?: number;
  targetDepth?: number;
  // When set, ``listProposedActivities`` returns this list + active.
  proposedItems?: unknown[];
  proposedActive?: unknown | null;
  // Per-endpoint delay (ms) so a test can race an abort against the
  // parallel fetches. Applied uniformly to all bootstrap reads.
  bootstrapDelayMs?: number;
}

function stubFullAuthFetch(opts: FetchOpts = {}): Mock {
  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const signal = init?.signal;
    if (url.endsWith("/api/auth/parent/status")) {
      return new Response(
        JSON.stringify({
          pin_set: true,
          locked: false,
          seconds_until_unlock: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/auth/parent") && method === "POST") {
      return new Response(
        JSON.stringify({
          token: "test-token",
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/metrics")) {
      return new Response(
        JSON.stringify({
          generated_at: "2026-05-10T12:00:00Z",
          ws_subscribers: 0,
          activities: {
            proposed_current: 0,
            approved_current: 0,
            running_current: 0,
            completed_current: 0,
            ended_current: 0,
            dismissed_current: 0,
            didnt_work_current: 0,
            last_24h: {},
          },
          transcripts: { total: 0, last_24h: 0 },
          audio: {
            mic_device: null,
            queue_depth: 0,
            buffer_overruns_total: 0,
            mic_enabled: true,
          },
          ai: {
            breaker_state: "closed",
            breaker_retry_after_iso: null,
            claude_capable: false,
            claude_capability_reason: "token_missing",
            listening_mode: 3,
            min_interval_throttle_seconds: 15.0,
          },
          activity_quality: {
            last_24h_mean_scores: {},
            judge_parent_agreement: {
              overlap_count: 0,
              agreement_rate: null,
              metric_name: "sign_agreement_rate",
            },
            safety_autofails_last_24h: 0,
          },
          eval_gate: {
            last_run_at: null,
            mean_dimension_scores: null,
            regressions_detected: 0,
            placeholder_baseline: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // The four parallel bootstrap reads. Each respects ``signal`` so
    // an abort surfaces as an AbortError-shaped rejection (which
    // ``Promise.allSettled`` then captures as a rejected result —
    // matching the production path).
    const respondWithDelay = async (body: unknown): Promise<Response> => {
      if (opts.bootstrapDelayMs !== undefined && opts.bootstrapDelayMs > 0) {
        await new Promise<void>((resolve, reject) => {
          const handle = setTimeout(resolve, opts.bootstrapDelayMs);
          if (signal !== undefined && signal !== null) {
            const onAbort = (): void => {
              clearTimeout(handle);
              const err = new Error("aborted");
              err.name = "AbortError";
              reject(err);
            };
            if (signal.aborted) {
              onAbort();
              return;
            }
            signal.addEventListener("abort", onAbort, { once: true });
          }
        });
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    if (url.endsWith("/api/settings/transcript-retention")) {
      return respondWithDelay({ seconds: opts.retentionSeconds ?? 60 });
    }
    if (url.endsWith("/api/settings/play-target-depth")) {
      return respondWithDelay({ value: opts.targetDepth ?? 3 });
    }
    if (url.endsWith("/api/activities/proposed?include_active=true")) {
      return respondWithDelay({
        items: opts.proposedItems ?? [],
        active: opts.proposedActive ?? null,
      });
    }
    // Phase K step K2: the eight feature-flag settings GETs joined
    // the parallel bootstrap. Match each kebab path and return the
    // spec'd seeded-default body shape ``{value: bool}``. ``true`` for
    // seven, ``false`` for play-spontaneity-enabled.
    const featureFlagDefaults: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/play-embedded-enabled": true,
      "/api/settings/play-endings-enabled": true,
      "/api/settings/play-spontaneity-enabled": false,
      "/api/settings/clickable-words-enabled": true,
      "/api/settings/read-me-button-enabled": true,
      // Phase Z Z6: neural-voice clip gate (default true).
      "/api/settings/neural-voice-enabled": true,
    };
    for (const [path, value] of Object.entries(featureFlagDefaults)) {
      if (url.endsWith(path)) {
        return respondWithDelay({ value });
      }
    }
    // Phase W Step W5: boss-fights flag bootstrap GET (default true).
    if (url.endsWith("/api/settings/boss-fights-enabled")) {
      return respondWithDelay({ value: true });
    }
    // Catch-all for any other initial fetch the bootstrap fires.
    return new Response(JSON.stringify({ items: [], next: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const fetchMock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function driveLoginToTabShell(): Promise<void> {
  await waitFor(() => {
    expect(screen.queryByTestId("pin-login")).toBeTruthy();
  });
  const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1234" } });
  fireEvent.click(screen.getByTestId("pin-login-submit"));
  await waitFor(() => {
    expect(screen.queryByTestId("tabs")).toBeTruthy();
  });
}

function fakeProposedActivity(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "act-bootstrap-proposed",
    state: "proposed",
    version: 1,
    title: "Bootstrap proposed",
    summary: null,
    persona_id: null,
    intent_source: null,
    child_ids: [],
    created_at: new Date().toISOString(),
    started_at: null,
    ended_at: null,
    steps: [],
    metadata: {},
    trigger_phrase: null,
    persona_reasoning: null,
    ...overrides,
  };
}

function fakeRunningActivity(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return fakeProposedActivity({
    id: "act-bootstrap-active",
    state: "running",
    title: "Bootstrap active",
    ...overrides,
  });
}

beforeEach(() => {
  useParentStore.setState({
    token: null,
    active: null,
    proposedList: [],
    wsState: "idle",
    toasts: [],
    capabilityReason: null,
  } as Partial<ReturnType<typeof useParentStore.getState>>);
  window.localStorage.clear();
});

describe("App bootstrap parallel-fetch happy path (J8)", () => {
  it("lifts all resolved values: proposedList + active into store, targetDepth fetched", async () => {
    const proposedItem = fakeProposedActivity();
    const activeItem = fakeRunningActivity();
    const fetchMock = stubFullAuthFetch({
      retentionSeconds: 300,
      targetDepth: 5,
      proposedItems: [proposedItem],
      proposedActive: activeItem,
    });
    render(<App />);
    await driveLoginToTabShell();
    // Bootstrap endpoints probed exactly once (cadence endpoint removed).
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(
      urls.filter((u) => u.endsWith("/api/settings/transcript-retention"))
        .length,
    ).toBe(1);
    expect(
      urls.filter((u) => u.endsWith("/api/settings/play-cadence-seconds"))
        .length,
    ).toBe(0);
    expect(
      urls.filter((u) => u.endsWith("/api/settings/play-target-depth"))
        .length,
    ).toBe(1);
    expect(
      urls.filter((u) =>
        u.endsWith("/api/activities/proposed?include_active=true"),
      ).length,
    ).toBe(1);
    // Phase L Step L8 + Phase Z Z6: the bootstrap parallel-fetch
    // seeds the SIX surviving feature flags exactly once each, even
    // though ``jokes_enabled`` + ``songs_enabled`` no longer drive
    // PlayFeaturesControls (they moved to RewardsSection). App.tsx's
    // lifted ``featureFlags`` dict is the single source of truth for
    // BOTH consumers; if a future regression dropped the jokes/songs
    // reads to "save a round-trip" the Rewards section header would
    // paint optimistic defaults instead of the persisted values.
    for (const path of [
      "/api/settings/jokes-enabled",
      "/api/settings/songs-enabled",
      "/api/settings/play-standalone-enabled",
      "/api/settings/clickable-words-enabled",
      "/api/settings/read-me-button-enabled",
      "/api/settings/neural-voice-enabled",
    ]) {
      expect(urls.filter((u) => u.endsWith(path)).length).toBe(1);
    }
    // proposedList + active hydrated from the REST snapshot via
    // applyMutationResult — the version-guarded reducer puts ``proposed``
    // rows into proposedList and ``running`` into active.
    await waitFor(() => {
      const store = useParentStore.getState();
      expect(store.proposedList).toHaveLength(1);
      expect(store.proposedList[0]?.id).toBe("act-bootstrap-proposed");
      expect(store.active?.id).toBe("act-bootstrap-active");
    });
    // cadenceSeconds is now a fixed 0 (disabled) passed directly to
    // PlayQueueList — no bootstrap fetch involved.
    await waitFor(() => {
      const latest = playQueueListPropsLog[playQueueListPropsLog.length - 1];
      expect(latest).toBeDefined();
      expect(latest!.cadenceSeconds).toBe(0);
    });
    // The WS client was constructed once after the parallel reads
    // settled — this is the un-aborted happy path.
    expect(parentWsClientCalls.length).toBe(1);
  });
});

describe("App bootstrap abort halts WS construction (J8)", () => {
  it("does NOT construct ParentWsClient when the aborter fires mid-bootstrap", async () => {
    // Delay the four parallel reads long enough for us to unmount
    // (which calls aborter.abort() in the bootstrap effect cleanup)
    // BEFORE they resolve. Without the ``aborter.signal.aborted``
    // guard after Promise.allSettled, the code would still fall
    // through to ``new ParentWsClient(...)``.
    stubFullAuthFetch({
      bootstrapDelayMs: 100,
    });
    const { unmount } = render(<App />);
    await waitFor(() => {
      expect(screen.queryByTestId("pin-login")).toBeTruthy();
    });
    // Drive the login flow but DO NOT wait for the tab shell — we
    // need to unmount while the four parallel reads are still in
    // flight (gated by ``bootstrapDelayMs``).
    const input = screen.getByTestId(
      "pin-login-pin-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1234" } });
    fireEvent.click(screen.getByTestId("pin-login-submit"));
    // Yield once so handleAuthSuccess → continueBootstrap fires its
    // pre-parallel-fetch awaits (health + metrics), then unmount mid-
    // ``Promise.allSettled``. Unmount fires the effect cleanup which
    // calls aborter.abort() — the parallel reads reject with
    // AbortError, allSettled captures them, and the guard returns
    // BEFORE the ws constructor.
    await new Promise((resolve) => setTimeout(resolve, 10));
    unmount();
    // Wait long enough for the (now-aborted) parallel reads to
    // settle. If the guard is missing, ParentWsClient would have been
    // constructed by now.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(parentWsClientCalls.length).toBe(0);
  });
});
